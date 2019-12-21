'''
common.py
Copyright (c) 2016-2019, Postgres Professional
'''

import psycopg2
import psycopg2.extensions
import select

# Some queries from TPC-DS may freeze or be even broken,
# so we allow some sort of failure, since we do not test
# Postgres, but rather that pg_query_state do not crash
# anything under stress load.
MAX_PG_QS_RETRIES = 50


def wait(conn):
	"""wait for some event on connection to postgres"""
	while 1:
		state = conn.poll()
		if state == psycopg2.extensions.POLL_OK:
			break
		elif state == psycopg2.extensions.POLL_WRITE:
			select.select([], [conn.fileno()], [])
		elif state == psycopg2.extensions.POLL_READ:
			select.select([conn.fileno()], [], [])
		else:
			raise psycopg2.OperationalError("poll() returned %s" % state)

def n_async_connect(config, n=1):
	"""establish n asynchronious connections to the postgres with specified config"""

	aconfig = config.copy()
	aconfig['async'] = True

	result = []
	for _ in range(n):
		conn = psycopg2.connect(**aconfig)
		wait(conn)
		result.append(conn)
	return result

def n_close(conns):
	"""close connections to postgres"""

	for conn in conns:
		conn.close()

def pg_query_state(config, pid, verbose=False, costs=False, timing=False, \
								buffers=False, triggers=False, format='text', \
								stress_in_progress=False):
	"""
	Get query state from backend with specified pid and optional parameters.
	Save any warning, info, notice and log data in global variable 'notices'
	"""

	conn = psycopg2.connect(**config)
	curs = conn.cursor()

	if stress_in_progress:
		set_guc(conn, 'statement_timeout', TPC_DS_STATEMENT_TIMEOUT)
		n_retries = 0

	result = []
	while not result:
		curs.callproc('pg_query_state', (pid, verbose, costs, timing, buffers, triggers, format))
		result = curs.fetchall()

		if stress_in_progress:
			n_retries += 1
			if n_retries >= MAX_PG_QS_RETRIES:
				print('\npg_query_state tried %s times with no effect, giving up' % MAX_PG_QS_RETRIES)
				break

	notices = conn.notices[:]
	conn.close()
	return result, notices

def query_state(config, async_conn, query, args={}, num_workers=0, stress_in_progress=False):
	"""
	Get intermediate state of 'query' on connection 'async_conn' after number of 'steps'
	of node executions from start of query
	"""

	acurs = async_conn.cursor()
	conn = psycopg2.connect(**config)
	curs = conn.cursor()

	set_guc(async_conn, 'enable_mergejoin', 'off')
	set_guc(async_conn, 'max_parallel_workers_per_gather', num_workers)
	acurs.execute(query)

	# extract current state of query progress
	pg_qs_args = {
			'config': config,
			'pid': async_conn.get_backend_pid()
			}
	for k, v in args.items():
		pg_qs_args[k] = v
	result, notices = pg_query_state(**pg_qs_args)
	wait(async_conn)

	set_guc(async_conn, 'pg_query_state.executor_trace', 'off')
	set_guc(async_conn, 'enable_mergejoin', 'on')

	conn.close()
	return result, notices

def set_guc(async_conn, param, value):
	acurs = async_conn.cursor()
	acurs.execute('set %s to %s' % (param, value))
	wait(async_conn)
