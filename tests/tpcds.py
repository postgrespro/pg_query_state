'''
test_cases.py
Copyright (c) 2016-2024, Postgres Professional
'''

import os
import subprocess
import time

import progressbar
# This actually imports progressbar2 but `import progressbar2' itself doesn't work.
# In case of problems with the progressbar/progressbar2, check that you have the
# progressbar2 installed and the path to it or venv is specified.

import psycopg2.extensions

import common

class DataLoadException(Exception): pass
class StressTestException(Exception): pass

def setup_tpcds(config):
	print('Setting up TPC-DS test...')
	subprocess.call(['./tests/prepare_stress.sh'])

	try:
		conn = psycopg2.connect(**config)
		cur = conn.cursor()

		# Create pg_query_state extension
		cur.execute('CREATE EXTENSION IF NOT EXISTS pg_query_state')

		# Create tables
		with open('tmp_stress/tpcds-kit/tools/tpcds.sql', 'r') as f:
			cur.execute(f.read())

		# Copy table data from files
		for table_datafile in os.listdir('tmp_stress/tpcds-kit/tools/'):
			if table_datafile.endswith('.dat'):
				table_name = os.path.splitext(os.path.basename(table_datafile))[0]

				print('Loading table', table_name)
				with open('tmp_stress/tpcds-kit/tools/tables/%s' % table_datafile) as f:
					cur.copy_from(f, table_name, sep='|', null='')

		conn.commit()

	except Exception as e:
		cur.close()
		conn.close()
		raise DataLoadException('Load failed: %s' % e)

	print('done!')

def run_tpcds(config):
	"""TPC-DS stress test"""

	TPC_DS_EXCLUDE_LIST = []			# actual numbers of TPC-DS tests to exclude
	TPC_DS_STATEMENT_TIMEOUT = 20000	# statement_timeout in ms

	print('Preparing TPC-DS queries...')
	err_count = 0
	queries = []
	for query_file in sorted(os.listdir('tmp_stress/tpcds-result-reproduction/query_qualification/')):
		with open('tmp_stress/tpcds-result-reproduction/query_qualification/%s' % query_file, 'r') as f:
			queries.append(f.read())

	acon, = common.n_async_connect(config)

	print('Starting TPC-DS queries...')
	timeout_list = []
	bar = progressbar.ProgressBar(max_value=len(queries))
	for i, query in enumerate(queries):
		bar.update(i + 1)
		if i + 1 in TPC_DS_EXCLUDE_LIST:
			continue
		try:
			# Set query timeout to TPC_DS_STATEMENT_TIMEOUT / 1000 seconds
			common.set_guc(acon, 'statement_timeout', TPC_DS_STATEMENT_TIMEOUT)

			# run query
			acurs = acon.cursor()
			acurs.execute(query)

			# periodically run pg_query_state on running backend trying to get
			# crash of PostgreSQL
			MAX_FIRST_GETTING_QS_RETRIES = 10
			PG_QS_DELAY, BEFORE_GETTING_QS_DELAY = 0.1, 0.1
			BEFORE_GETTING_QS, GETTING_QS = range(2)
			state, n_first_getting_qs_retries = BEFORE_GETTING_QS, 0

			pg_qs_args = {
				'config': config,
				'pid': acon.get_backend_pid()
			}

			while True:
				try:
					result, notices = common.pg_query_state(**pg_qs_args)
				except Exception as e:
					# do not consider the test failed if the "error in message
					# queue data transmitting" is received, this may happen with
					# some small probability, but if it happens too often it is
					# a problem, we will handle this case after the loop
					if "error in message queue data transmitting" in e.pgerror:
						err_count += 1
					else:
						raise e

				# run state machine to determine the first getting of query state
				# and query finishing
				if state == BEFORE_GETTING_QS:
					if len(result) > 0 or common.BACKEND_IS_ACTIVE_INFO in notices:
						state = GETTING_QS
						continue
					n_first_getting_qs_retries += 1
					if n_first_getting_qs_retries >= MAX_FIRST_GETTING_QS_RETRIES:
						# pg_query_state callings don't return any result, more likely run
						# query has completed
						break
					time.sleep(BEFORE_GETTING_QS_DELAY)
				elif state == GETTING_QS:
					if common.BACKEND_IS_IDLE_INFO in notices:
						break
					time.sleep(PG_QS_DELAY)

			# wait for real query completion
			common.wait(acon)

		except psycopg2.extensions.QueryCanceledError:
			timeout_list.append(i + 1)

	if err_count > 2:
		print("ERROR: error in message queue data transmitting")
		raise
	elif err_count > 0:
		print(err_count, " times there was error in message queue data transmitting")

	common.n_close((acon,))

	if len(timeout_list) > 0:
		print('\nThere were pg_query_state timeouts (%s s) on queries:' % TPC_DS_STATEMENT_TIMEOUT, timeout_list)
