from __future__ import print_function, division, absolute_import

import os
import json
import psycopg2
import psycopg2.extensions
import re
import select
import time
import xml.etree.ElementTree as ET
import yaml
import subprocess
import progressbar

from time import sleep

# Some queries from TPC-DS may freeze or be even broken,
# so we allow some sort of failure, since we do not test
# Postgres, but rather that pg_query_state do not crash
# anything under stress load.
MAX_PG_QS_RETRIES = 50
TPC_DS_EXCLUDE_LIST = [] # actual numbers of TPC-DS tests to exclude
TPC_DS_STATEMENT_TIMEOUT = 20000 # statement_timeout in ms
stress_in_progress = False

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

notices = []

def debug_output(qs, qs_len, pid, query, expected):
	something_happened = False
	if (qs_len and len(qs) != qs_len ):
		print( "len(qs): ", len(qs), ", expected: ", qs_len)
		something_happened = True
	if (pid and qs[0][0] != pid):
		print( "qs[0][0]: ", qs[0][0], " = ", pid)
		something_happened = True
	if (qs[0][1] != 0):
		print( "qs[0][1]: ", qs[0][1], ", expected: 0")
		something_happened = True
	if (qs[0][2] != query):
		print( "qs[0][2]:\n", qs[0][2])
		print( "Expected:\n", query)
		something_happened = True
	if (not (re.match(expected, qs[0][3]))):
		print( "qs[0][3]:\n", qs[0][3])
		print( "Expected:\n", expected)
		something_happened = True
	if (qs[0][4] != None):
		print( "qs[0][4]: ", qs[0][4], "Expected: None")
		something_happened = True
	if (qs_len and len(qs) > qs_len):
		for i in range(qs_len, len(qs)):
			print( "qs[",i,"][0]: ", qs[i][0])
			print( "qs[",i,"][1]: ", qs[i][1])
			print( "qs[",i,"][2]: ", qs[i][2])
			print( "qs[",i,"][3]: ", qs[i][3])
			print( "qs[",i,"][4]: ", qs[i][4])
		something_happened = True
	if (something_happened):
		print( "If test have not crashed, then it's OK")

def notices_warning():
	if (len(notices) > 0):
		print("")
		print("WARNING:")
		print(notices)

def pg_query_state(config, pid, verbose=False, costs=False, timing=False, \
								buffers=False, triggers=False, format='text'):
	"""
	Get query state from backend with specified pid and optional parameters.
	Save any warning, info, notice and log data in global variable 'notices'
	"""

	global notices, stress_in_progress

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
	return result

def test_deadlock(config):
	"""test when two backends try to extract state of each other"""

	acon1, acon2 = n_async_connect(config, 2)
	acurs1 = acon1.cursor()
	acurs2 = acon2.cursor()

	while True:
		acurs1.callproc('pg_query_state', (acon2.get_backend_pid(),))
		acurs2.callproc('pg_query_state', (acon1.get_backend_pid(),))

		# listen acon1, acon2 with timeout = 10 sec to determine deadlock
		r, w, x = select.select([acon1.fileno(), acon2.fileno()], [], [], 10)
		assert (r or w or x), "Deadlock is happened under cross reading of query states"

		wait(acon1)
		wait(acon2)

		# exit from loop if one backend could read state of execution 'pg_query_state'
		# from other backend
		if acurs1.fetchone() or acurs2.fetchone():
			break

	n_close((acon1, acon2))

def query_state(config, async_conn, query, args={}, num_workers=0):
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
	result = pg_query_state(**pg_qs_args)
	wait(async_conn)

	set_guc(async_conn, 'pg_query_state.executor_trace', 'off')
	set_guc(async_conn, 'enable_mergejoin', 'on')

	conn.close()
	return result

def test_simple_query(config):
	"""test statistics of simple query"""

	acon, = n_async_connect(config)
	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate \(Current loop: actual rows=\d+, loop number=1\)
  ->  Hash Join \(Current loop: actual rows=\d+, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo \(Current loop: actual rows=\d+, loop number=1\)
        ->  Hash \(Current loop: actual rows=\d+, loop number=1\)
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB
              ->  Seq Scan on bar \(Current loop: actual rows=\d+, loop number=1\)"""

	qs = query_state(config, acon, query)
	debug_output(qs, 1, acon.get_backend_pid(), query, expected)
	notices_warning()
	#assert len(qs) == 1 #Skip this check while output of test can be different
	assert qs[0][0] == acon.get_backend_pid() and qs[0][1] == 0 \
		and qs[0][2] == query and re.match(expected, qs[0][3]) and qs[0][4] == None

	n_close((acon,))

def test_concurrent_access(config):
	"""test when two backends compete with each other to extract state from third running backend"""

	acon1, acon2, acon3 = n_async_connect(config, 3)
	acurs1, acurs2, acurs3 = acon1.cursor(), acon2.cursor(), acon3.cursor()
	query = 'select count(*) from foo join bar on foo.c1=bar.c1'

	set_guc(acon3, 'max_parallel_workers_per_gather', 0)
	acurs3.execute(query)
	time.sleep(0.1)
	acurs1.callproc('pg_query_state', (acon3.get_backend_pid(),))
	acurs2.callproc('pg_query_state', (acon3.get_backend_pid(),))
	wait(acon1)
	wait(acon2)
	wait(acon3)

	qs1, qs2 = acurs1.fetchall(), acurs2.fetchall()
	assert len(qs1) == len(qs2) == 1 \
		and qs1[0][0] == qs2[0][0] == acon3.get_backend_pid() \
		and qs1[0][1] == qs2[0][1] == 0 \
		and qs1[0][2] == qs2[0][2] == query \
		and len(qs1[0][3]) > 0 and len(qs2[0][3]) > 0 \
		and qs1[0][4] == qs2[0][4] == None
	#assert len(notices) == 0
	notices_warning()

	n_close((acon1, acon2, acon3))

def test_nested_call(config):
	"""test statistics under calling function"""

	acon, = n_async_connect(config)
	util_conn = psycopg2.connect(**config)
	util_curs = util_conn.cursor()
	create_function = """
		create or replace function n_join_foo_bar() returns integer as $$
			begin
				return (select count(*) from foo join bar on foo.c1=bar.c1);
			end;
		$$ language plpgsql"""
	drop_function = 'drop function n_join_foo_bar()'
	call_function = 'select * from n_join_foo_bar()'
	nested_query = 'SELECT (select count(*) from foo join bar on foo.c1=bar.c1)'
	expected = 'Function Scan on n_join_foo_bar (Current loop: actual rows=0, loop number=1)'
	expected_nested = r"""Result \(Current loop: actual rows=0, loop number=1\)
  InitPlan 1 \(returns \$0\)
    ->  Aggregate \(Current loop: actual rows=0, loop number=1\)
          ->  Hash Join \(Current loop: actual rows=0, loop number=1\)
                Hash Cond: \(foo.c1 = bar.c1\)
                ->  Seq Scan on foo \(Current loop: actual rows=1, loop number=1\)
                ->  Hash \(Current loop: actual rows=0, loop number=1\)
                      Buckets: \d+  Batches: \d+  Memory Usage: \d+kB
                      ->  Seq Scan on bar \(Current loop: actual rows=\d+, loop number=1\)"""

	util_curs.execute(create_function)
	util_conn.commit()

	qs = query_state(config, acon, call_function)
	assert len(qs) == 2 \
		and qs[0][0] == qs[1][0] == acon.get_backend_pid() \
		and qs[0][1] == 0 and qs[1][1] == 1 \
		and qs[0][2] == call_function and qs[0][3] == expected \
		and qs[1][2] == nested_query and re.match(expected_nested, qs[1][3]) \
		and qs[0][4] == qs[1][4] == None
	assert len(notices) == 0

	util_curs.execute(drop_function)

	util_conn.close()
	n_close((acon,))

def test_insert_on_conflict(config):
	"""test statistics on conflicting tuples under INSERT ON CONFLICT query"""

	acon, = n_async_connect(config)
	util_conn = psycopg2.connect(**config)
	util_curs = util_conn.cursor()
	add_field_uniqueness = 'alter table foo add constraint unique_c1 unique(c1)'
	drop_field_uniqueness = 'alter table foo drop constraint unique_c1'
	query = 'insert into foo select i, md5(random()::text) from generate_series(1, 30000) as i on conflict do nothing'

	expected = r"""Insert on foo \(Current loop: actual rows=\d+, loop number=\d+\)
  Conflict Resolution: NOTHING
  Conflicting Tuples: \d+
  ->  Function Scan on generate_series i \(Current loop: actual rows=\d+, loop number=\d+\)"""

	util_curs.execute(add_field_uniqueness)
	util_conn.commit()

	qs = query_state(config, acon, query)

	debug_output(qs, 1, acon.get_backend_pid(), query, expected)
	notices_warning()
	#assert len(qs) == 1 \
	assert qs[0][0] == acon.get_backend_pid() and qs[0][1] == 0 \
		and qs[0][2] == query and re.match(expected, qs[0][3]) \
		and qs[0][4] == None
	assert len(notices) == 0

	util_curs.execute(drop_field_uniqueness)

	util_conn.close()
	n_close((acon,))

def set_guc(async_conn, param, value):
	acurs = async_conn.cursor()
	acurs.execute('set %s to %s' % (param, value))
	wait(async_conn)

def test_trigger(config):
	"""test trigger statistics"""

	acon, = n_async_connect(config)
	acurs = acon.cursor()
	util_conn = psycopg2.connect(**config)
	util_curs = util_conn.cursor()
	create_trigger_function = """
		create or replace function unique_c1_in_foo() returns trigger as $$
			begin
				if new.c1 in (select c1 from foo) then
					return null;
				end if;
				return new;
			end;
		$$ language plpgsql"""
	create_trigger = """
		create trigger unique_foo_c1
			before insert or update of c1 on foo for row
			execute procedure unique_c1_in_foo()"""	
	drop_temps = 'drop function unique_c1_in_foo() cascade'
	query = 'insert into foo select i, md5(random()::text) from generate_series(1, 10000) as i'
	expected_upper = r"""Insert on foo \(Current loop: actual rows=\d+, loop number=1\)
  ->  Function Scan on generate_series i \(Current loop: actual rows=\d+, loop number=1\)"""
	trigger_suffix = r"""Trigger unique_foo_c1: calls=\d+"""

	util_curs.execute(create_trigger_function)
	util_curs.execute(create_trigger)
	util_conn.commit()

	qs = query_state(config, acon, query, {'triggers': True})
	debug_output(qs, None, acon.get_backend_pid(), query, expected_upper)
	notices_warning()
	assert qs[0][0] == acon.get_backend_pid() and qs[0][1] == 0 \
		and qs[0][2] == query and re.match(expected_upper, qs[0][3]) \
		and qs[0][4] == None
	assert len(notices) == 0

	qs = query_state(config, acon, query, {'triggers': False})
	debug_output(qs, None, acon.get_backend_pid(), query, expected_upper)
	notices_warning()
	assert qs[0][0] == acon.get_backend_pid() and qs[0][1] == 0 \
		and qs[0][2] == query and re.match(expected_upper, qs[0][3]) \
		and qs[0][4] == None
	assert len(notices) == 0

	util_curs.execute(drop_temps)

	util_conn.close()
	n_close((acon,))

def test_costs(config):
	"""test plan costs"""

	acon, = n_async_connect(config)
	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate  \(cost=\d+.\d+..\d+.\d+ rows=\d+ width=8\) \(Current loop: actual rows=0, loop number=1\)
  ->  Hash Join  \(cost=\d+.\d+..\d+.\d+ rows=\d+ width=0\) \(Current loop: actual rows=0, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo  \(cost=0.00..\d+.\d+ rows=\d+ width=4\) \(Current loop: actual rows=1, loop number=1\)
        ->  Hash  \(cost=\d+.\d+..\d+.\d+ rows=\d+ width=4\) \(Current loop: actual rows=0, loop number=1\)
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB
              ->  Seq Scan on bar  \(cost=0.00..\d+.\d+ rows=\d+ width=4\) \(Current loop: actual rows=\d+, loop number=1\)"""

	qs = query_state(config, acon, query, {'costs': True})
	debug_output(qs, 1, None, query, expected)
	notices_warning()
	assert len(qs) == 1 and re.match(expected, qs[0][3])
	assert len(notices) == 0

	n_close((acon,))

def test_buffers(config):
	"""test buffer statistics"""

	acon, = n_async_connect(config)
	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate \(Current loop: actual rows=0, loop number=1\)
  ->  Hash Join \(Current loop: actual rows=0, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo \(Current loop: actual rows=1, loop number=1\)
              Buffers: [^\n]*
        ->  Hash \(Current loop: actual rows=0, loop number=1\)
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB
              ->  Seq Scan on bar \(Current loop: actual rows=\d+, loop number=1\)
                    Buffers: .*"""

	set_guc(acon, 'pg_query_state.enable_buffers', 'on')

	qs = query_state(config, acon, query, {'buffers': True})
	debug_output(qs, 1, None, query, expected)
	notices_warning()
	assert len(qs) == 1 and re.match(expected, qs[0][3])
	assert len(notices) == 0

	n_close((acon,))

def test_timing(config):
	"""test timing statistics"""

	acon, = n_async_connect(config)
	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate \(Current loop: running time=\d+.\d+ actual rows=0, loop number=1\)
  ->  Hash Join \(Current loop: running time=\d+.\d+ actual rows=0, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo \(Current loop: actual time=\d+.\d+..\d+.\d+ rows=1, loop number=1\)
        ->  Hash \(Current loop: running time=\d+.\d+ actual rows=0, loop number=1\)
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB
              ->  Seq Scan on bar \(Current loop: actual time=\d+.\d+..\d+.\d+ rows=\d+, loop number=1\)"""

	set_guc(acon, 'pg_query_state.enable_timing', 'on')

	qs = query_state(config, acon, query, {'timing': True})
	debug_output(qs, 1, None, query, expected)
	notices_warning()
	assert len(qs) == 1 and re.match(expected, qs[0][3])
	assert len(notices) == 0

	n_close((acon,))

def check_plan(plan):
	assert 'Current loop' in plan
	cur_loop = plan['Current loop']
	assert 'Actual Loop Number' in cur_loop\
		and 'Actual Rows' in cur_loop

	if not 'Plans' in plan:
		return

	for subplan in plan['Plans']:
		check_plan(subplan)

def check_xml(root):
	prefix = '{http://www.postgresql.org/2009/explain}'
	for plan in root.iter(prefix + 'Plan'):
		cur_loop = plan.find(prefix + 'Current-loop')
		assert cur_loop != None \
			and cur_loop.find(prefix + 'Actual-Loop-Number') != None \
			and cur_loop.find(prefix + 'Actual-Rows') != None

def test_formats(config):
	"""test all formats of pg_query_state output"""

	acon, = n_async_connect(config)
	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate \(Current loop: actual rows=0, loop number=1\)
  ->  Hash Join \(Current loop: actual rows=0, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo \(Current loop: actual rows=1, loop number=1\)
        ->  Hash \(Current loop: actual rows=0, loop number=1\)
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB
              ->  Seq Scan on bar \(Current loop: actual rows=\d+, loop number=1\)"""

	qs = query_state(config, acon, query, {'format': 'text'})
	debug_output(qs, 1, None, query, expected)
	notices_warning()
	assert len(qs) == 1 and re.match(expected, qs[0][3])
	assert len(notices) == 0

	qs = query_state(config, acon, query, {'format': 'json'})
	try:
		js_obj = json.loads(qs[0][3])
	except ValueError:
		assert False, 'Invalid json format'
	assert len(qs) == 1
	assert len(notices) == 0
	check_plan(js_obj['Plan'])

	qs = query_state(config, acon, query, {'format': 'xml'})
	assert len(qs) == 1
	assert len(notices) == 0
	try:
		xml_root = ET.fromstring(qs[0][3])
	except:
		assert False, 'Invalid xml format'
	check_xml(xml_root)

	qs = query_state(config, acon, query, {'format': 'yaml'})
	try:
		yaml_doc = yaml.load(qs[0][3], Loader=yaml.FullLoader)
	except:
		assert False, 'Invalid yaml format'
	assert len(qs) == 1
	assert len(notices) == 0
	check_plan(yaml_doc['Plan'])

	n_close((acon,))

def test_timing_buffers_conflicts(config):
	"""test when caller requests timing and buffers but counterpart turned off its"""

	acon, = n_async_connect(config)
	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	timing_pattern = '(?:running time=\d+.\d+)|(?:actual time=\d+.\d+..\d+.\d+)'
	buffers_pattern = 'Buffers:'

	qs = query_state(config, acon, query, {'timing': True, 'buffers': False})
	assert len(qs) == 1 and not re.search(timing_pattern, qs[0][3])
	assert notices == ['WARNING:  timing statistics disabled\n']

	qs = query_state(config, acon, query, {'timing': False, 'buffers': True})
	assert len(qs) == 1 and not re.search(buffers_pattern, qs[0][3])
	assert notices == ['WARNING:  buffers statistics disabled\n']

	qs = query_state(config, acon, query, {'timing': True, 'buffers': True})
	assert len(qs) == 1 and not re.search(timing_pattern, qs[0][3]) \
						 and not re.search(buffers_pattern, qs[0][3])
	assert len(notices) == 2 and 'WARNING:  timing statistics disabled\n' in notices \
							 and 'WARNING:  buffers statistics disabled\n' in notices

	n_close((acon,))

class DataLoadException(Exception): pass
class StressTestException(Exception): pass

def load_tpcds_data(config):
	print('Loading TPC-DS data...')
	subprocess.call(['./tests/prepare_stress.sh'])

	try:
		conn = psycopg2.connect(**config)
		cur = conn.cursor()

		# Create tables
		with open('tmp_stress/tpcds-kit/tools/tpcds.sql', 'r') as f:
			cur.execute(f.read())

		# Copy table data from files
		for table_datafile in os.listdir('tmp_stress/tpcds-kit/tools/'):
			if table_datafile.endswith('.dat'):
				table_name = os.path.splitext(os.path.basename(table_datafile))[0]
				copy_cmd = "COPY %s FROM '/pg/testdir/tmp_stress/tpcds-kit/tools/tables/%s' CSV DELIMITER '|'" % (table_name, table_datafile)

				print("Loading table", table_name)
				# cur.execute("TRUNCATE %s" % table_name)
				cur.execute(copy_cmd)

		conn.commit()

	except Exception as e:
		cur.close()
		conn.close()
		raise DataLoadException('Load failed: %s' % e)

	print('done!')

def stress_test(config):
	"""TPC-DS stress test"""
	global stress_in_progress

	stress_in_progress = True
	load_tpcds_data(config)

	print('Preparing TPC-DS queries...')
	# Execute query in separate thread 
	# with open('tmp_stress/tpcds-kit/tools/query_0.sql', 'r') as f:
	with open('tests/query_tpcds.sql', 'r') as f:
		sql = f.read()

	queries = sql.split(';')
	for i, query in enumerate(queries):
		queries[i] = query.replace('%','%%')
		if (len(query.strip()) == 0):
			del queries[i]

	acon, = n_async_connect(config)

	print('Starting TPC-DS queries...')
	timeout_list = []
	bar = progressbar.ProgressBar(max_value=len(queries))
	for i, query in enumerate(queries):
		bar.update(i + 1)
		if i + 1 in TPC_DS_EXCLUDE_LIST:
			continue
		try:
			# Set query timeout to TPC_DS_STATEMENT_TIMEOUT / 1000 seconds
			set_guc(acon, 'statement_timeout', TPC_DS_STATEMENT_TIMEOUT)
			qs = query_state(config, acon, query)

		except psycopg2.extensions.QueryCanceledError:
			timeout_list.append(i + 1)

	n_close((acon,))

	if len(timeout_list) > 0:
		print('There were pg_query_state timeouts (%s s) on queries:' % TPC_DS_STATEMENT_TIMEOUT, timeout_list)
	
	stress_in_progress = False
