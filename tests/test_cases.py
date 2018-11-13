import testgres
import psycopg2.extensions
import json
import re
import time
import xml.etree.ElementTree as ET
import yaml
from time import sleep
from multiprocessing import Process, Queue, Condition
import subprocess
import os
import progressbar

class SQLExecuteException(Exception): pass

class AsyncQueryExecutor():
	""" Run query in separate process """
	process = Process()
	def __init__(self, node):
		self.node = node
		self.conn = testgres.connection.NodeConnection(node)
		self.cursor = self.conn.cursor
		self.backend_pid = self.conn.pid
		self.res_q = Queue()
		self.condition = Condition()
		self.result = []

	def run_internal(self, conn, query, res_q, condition):
		condition.acquire()
		conn.begin()
		condition.notify()
		condition.release()
		try:
			res = conn.execute(query)
			res_q.put(res)
			conn.commit()
		except Exception, e:
			conn.rollback()
			print 'Unable to execute query: "', query ,'"'
			raise SQLExecuteException(
				'Unable to execute query: "%s"\nReason: %s' %(query, e))
		return

	def run_stress(self, conn, query, condition):
		condition.acquire()
		condition.notify()
		condition.release()
		i = 1
		try:
			conn.begin()
			conn.execute(query)
			conn.commit()
		except psycopg2.extensions.QueryCanceledError:
			pass
		except Exception, e:
			print "Unable to execute: %s" %query
			print "Reason: %s" %e

	def run(self, query, multiple = False):
		"""Run async query"""
		if multiple:
			self.process = Process(target=self.run_stress,
							args=(self.conn, query, self.condition))
		else:
			self.process = Process(target=self.run_internal,
							args=(self.conn, query, self.res_q, self.condition))
		self.process.start()
		self.condition.acquire()
		self.condition.wait(5)

	def wait(self):
		self.result = self.res_q.get()
		if self.process.is_alive():
			self.process.join()

	def terminate(self):
		if self.node.pid:
			self.node.psql("SELECT pg_cancel_backend(%d)" % self.backend_pid)
		if self.process.is_alive():
			self.process.terminate()

	def close(self):
		self.terminate()
		if self.node.pid:
			self.conn.close()

def pqs_args(pid, verbose=False, costs=False, timing=False,
			 buffers=False, triggers=False, format='text'):
	s = "%d, %s, %s, %s, %s, %s, '%s'" % (pid, verbose, costs, timing, buffers, 
										triggers, format)
	return s

def set_guc(conn, param, value):
	conn.execute('set %s to %s' % (param, value))
	conn.commit()

def query_state(node, query, args={}, gucs={}, num_workers=0, expected_len=1, delay = 0):
	"""
	Get intermediate state of 'query' on connection 'async_conn' after number of 'steps'
	of node executions from start of query
	"""

	result = []
	aq = AsyncQueryExecutor(node)
	conn = testgres.connection.NodeConnection(node)

	gucs.update({'enable_mergejoin' : 'off'})
	gucs.update({'max_parallel_workers_per_gather' : num_workers})
	for param, value in gucs.items():
		set_guc(aq.conn, param, value)

	aq.run(query)
	sleep(delay)
	# extract current state of query progress
	while len(result) < expected_len and aq.process.is_alive():
		result = conn.execute(r"""SELECT pid, 
										 frame_number, 
										 query_text, 
										 plan, 
										 leader_pid 
								  FROM pg_query_state(%s);""" %
							  pqs_args(aq.backend_pid, **args))
	aq.wait()
	aq.close()
	conn.close()
	assert	result[0][0] == aq.backend_pid and result[0][1] == 0 \
		and result[0][2] == query and result[0][4] == None

	return result

def debug_output(qs, qs_len, pid, query, expected, expected2 = None):
	something_happened = False
	if (qs_len and len(qs) != qs_len ):
		print "len(qs): ", len(qs), ", expected: ", qs_len
		something_happened = True
	if (pid and qs[0][0] != pid):
		print "qs[0][0]: ", qs[0][0], " = ", pid
		something_happened = True
	if (qs[0][1] != 0):
		print "qs[0][1]: ", qs[0][1], ", expected: 0"
		something_happened = True
	if (qs[0][2] != query):
		print "qs[0][2]:\n", qs[0][2]
		print "Expected:\n", query
		something_happened = True
	if (expected and not (re.match(expected, qs[0][3]))):
		print "qs[0][3]:\n", qs[0][3]
		print "Expected:\n", expected
		something_happened = True
	if (qs_len == 2 and expected2 and
		not (re.match(expected2, qs[1][3]))):
		print "qs[1][3]:\n", qs[1][3]
		print "Expected:\n", expected2
		something_happened = True
	if (qs[0][4] != None):
		print "qs[0][4]: ", qs[0][4], "Expected: None"
		something_happened = True
	if (qs_len and len(qs) > qs_len):
		for i in range(qs_len, len(qs)):
			print "qs[",i,"][0]: ", qs[i][0]
			print "qs[",i,"][1]: ", qs[i][1]
			print "qs[",i,"][2]: ", qs[i][2]
			print "qs[",i,"][3]: ", qs[i][3]
			print "qs[",i,"][4]: ", qs[i][4]
		something_happened = True
	if (something_happened):
		print "If test have not crashed, then it's OK"

def test_deadlock(node):
	"""test when two backends try to extract state of each other"""

	async_query1 = AsyncQueryExecutor(node)
	async_query2 = AsyncQueryExecutor(node)

	for try_n in range(10):
		async_query1.run("select pg_query_state(%d)" % async_query2.backend_pid)
		async_query2.run("select pg_query_state(%d)" % async_query1.backend_pid)

		async_query1.wait()
		async_query2.wait()
		# exit from loop if one backend could read state of execution 'pg_query_state'
		# from other backend
		if async_query1.result or async_query1.result:
			break
			expected = "Deadlock is happened under cross reading of query states"
			assert re.match(expected, async_query1.result) or (re.search(expected, async_query2.result))

	async_query1.close()
	async_query2.close()

def test_simple_query(node):
	"""test statistics of simple query"""

	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate \(Current loop: actual rows=\d+, loop number=1\)
  ->  Hash Join \(Current loop: actual rows=\d+, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo \(Current loop: actual rows=\d+, loop number=1\)
        ->  Hash \(Current loop: actual rows=\d+, loop number=1\)(
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB)?
              ->  Seq Scan on bar \(Current loop: actual rows=\d+, loop number=1\)"""

	qs = query_state(node, query)
	debug_output(qs, 1, None, query, expected)
	assert	re.match(expected, qs[0][3])

def test_concurrent_access(node):
	"""test when two backends compete with each other to extract state from third running backend"""

	acon1 = AsyncQueryExecutor(node)
	acon2 = AsyncQueryExecutor(node) 
	acon3 = AsyncQueryExecutor(node)

	query = 'select count(*) from foo join bar on foo.c1=bar.c1'

	set_guc(acon3.conn, 'max_parallel_workers_per_gather', 0)
	acon3.run(query)

	psq_query = r"""SELECT pid, 
						   frame_number, 
						   query_text, 
						   plan, 
						   leader_pid 
					FROM pg_query_state(%s);""" % pqs_args(acon3.backend_pid)
	
	while len(acon1.result) < 1 and acon3.process.is_alive():
		acon1.run(psq_query)
		acon2.run(psq_query)
		acon1.wait()
		acon2.wait()
	acon3.terminate()

	qs1 = acon1.result 
	qs2 = acon2.result

	assert acon3.backend_pid == qs1[0][0]
	assert acon3.backend_pid == qs2[0][0]

	acon1.close()
	acon2.close()
	acon3.close()

def test_nested_call(node):
	"""test statistics under calling function"""

	conn = testgres.connection.NodeConnection(node)

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
    ->  Aggregate \(Current loop: actual rows=\d+, loop number=1\)
          ->  Hash Join \(Current loop: actual rows=\d+, loop number=1\)
                Hash Cond: \(foo.c1 = bar.c1\)
                ->  Seq Scan on foo \(Current loop: actual rows=\d+, loop number=1\)
                ->  Hash \(Current loop: actual rows=\d+, loop number=1\)(
                      Buckets: \d+  Batches: \d+  Memory Usage: \d+kB)?
                      ->  Seq Scan on bar \(Current loop: actual rows=\d+, loop number=1\)"""

	conn.execute(create_function)
	conn.commit()

	qs = query_state(node, call_function, expected_len = 2)
	debug_output(qs, 2, None, call_function, None, expected_nested)
	assert 	len(qs) == 2 \
		and qs[0][1] == 0 and qs[1][1] == 1 \
		and qs[0][2] == call_function and qs[0][3] == expected \
		and qs[1][2] == nested_query and re.match(expected_nested, qs[1][3]) \
		and qs[0][4] == qs[1][4] == None

	conn.execute(drop_function)
	conn.commit()
	conn.close()

def test_insert_on_conflict(node):
	"""test statistics on conflicting tuples under INSERT ON CONFLICT query"""

	util_conn = testgres.connection.NodeConnection(node)
	add_field_uniqueness = 'alter table foo add constraint unique_c1 unique(c1)'
	drop_field_uniqueness = 'alter table foo drop constraint unique_c1'
	query = 'insert into foo select i, md5(random()::text) from generate_series(1, 30000) as i on conflict do nothing'

	expected = r"""Insert on foo \(Current loop: actual rows=\d+, loop number=\d+\)
  Conflict Resolution: NOTHING
  Conflicting Tuples: \d+
  ->  Function Scan on generate_series i \(Current loop: actual rows=\d+, loop number=\d+\)"""

	util_conn.execute(add_field_uniqueness)
	util_conn.commit()

	qs = query_state(node, query)
	debug_output(qs, None, None, query, expected)
	assert re.match(expected, qs[0][3])

	util_conn.execute(drop_field_uniqueness)
	util_conn.close()

def test_trigger(node):
	"""test trigger statistics"""

	util_conn = testgres.connection.NodeConnection(node)

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

	util_conn.execute(create_trigger_function)
	util_conn.execute(create_trigger)
	util_conn.commit()

	qs = query_state(node, query, {'triggers': True}, delay = 1)
	debug_output(qs, None, None, query, expected_upper+'\n'+ trigger_suffix)
	assert re.match(expected_upper+'\n'+ trigger_suffix, qs[0][3])
	qs = query_state(node, query, {'triggers': False})
	debug_output(qs, None, None, query, expected_upper)
	assert re.match(expected_upper, qs[0][3])

	util_conn.execute(drop_temps)
	util_conn.close()

def test_costs(node):
	"""test plan costs"""

	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate  \(cost=\d+.\d+..\d+.\d+ rows=\d+ width=8\) \(Current loop: actual rows=0, loop number=1\)
  ->  Hash Join  \(cost=\d+.\d+..\d+.\d+ rows=\d+ width=0\) \(Current loop: actual rows=\d+, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo  \(cost=0.00..\d+.\d+ rows=\d+ width=4\) \(Current loop: actual rows=\d+, loop number=1\)
        ->  Hash  \(cost=\d+.\d+..\d+.\d+ rows=\d+ width=4\) \(Current loop: actual rows=\d+, loop number=1\)(
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB)?
              ->  Seq Scan on bar  \(cost=0.00..\d+.\d+ rows=\d+ width=4\) \(Current loop: actual rows=\d+, loop number=1\)"""

	qs = query_state(node, query, {'costs': True})
	debug_output(qs, 1, None, query, expected)
	assert len(qs) == 1 
	assert re.match(expected, qs[0][3])

def test_buffers(config):
	"""test buffer statistics"""

	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate \(Current loop: actual rows=0, loop number=1\)
  ->  Hash Join \(Current loop: actual rows=\d+, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo \(Current loop: actual rows=\d+, loop number=1\)(
              Buffers: [^\n]*)?
        ->  Hash \(Current loop: actual rows=\d+, loop number=1\)(
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB)?
              ->  Seq Scan on bar \(Current loop: actual rows=\d+, loop number=1\)(
                    Buffers: .*)?"""

	qs = query_state(config, query, {'buffers': True},
					 gucs = {'pg_query_state.enable_buffers' : 'on'})
	debug_output(qs, 1, None, query, expected)
	assert 	len(qs) == 1 and re.match(expected, qs[0][3])

def test_timing(node):
	"""test timing statistics"""

	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate \(Current loop: running time=\d+.\d+ actual rows=0, loop number=1\)
  ->  Hash Join \(Current loop: running time=\d+.\d+ actual rows=\d+, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo \(Current loop: (actual|running) time=\d+.\d+(..\d+.\d+)? (actual )?rows=\d+, loop number=1\)
        ->  Hash \(Current loop: running time=\d+.\d+ actual rows=\d+, loop number=1\)(
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB)?
              ->  Seq Scan on bar \(Current loop: (actual|running) time=\d+.\d+(..\d+.\d+)* (actual )*rows=\d+, loop number=1\)"""

	qs = query_state(node, query, {'timing': True},
					  gucs={'pg_query_state.enable_timing' : 'on'})
	debug_output(qs, 1, None, query, expected)
	assert 	len(qs) == 1 and re.match(expected, qs[0][3])

def check_plan(plan):
	assert 	plan.has_key('Current loop')
	cur_loop = plan['Current loop']
	assert 	cur_loop.has_key('Actual Loop Number') \
		and cur_loop.has_key('Actual Rows')

	if not plan.has_key('Plans'):
		return

	for subplan in plan['Plans']:
		check_plan(subplan)

def check_xml(root):
	prefix = '{http://www.postgresql.org/2009/explain}'
	for plan in root.iter(prefix + 'Plan'):
		cur_loop = plan.find(prefix + 'Current-loop')
		assert 	cur_loop != None \
			and cur_loop.find(prefix + 'Actual-Loop-Number') != None \
			and cur_loop.find(prefix + 'Actual-Rows') != None

def test_formats(config):
	"""test all formats of pg_query_state output"""

	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	expected = r"""Aggregate \(Current loop: actual rows=\d+, loop number=1\)
  ->  Hash Join \(Current loop: actual rows=\d+, loop number=1\)
        Hash Cond: \(foo.c1 = bar.c1\)
        ->  Seq Scan on foo \(Current loop: actual rows=\d, loop number=1\)
        ->  Hash \(Current loop: actual rows=\d+, loop number=1\)(
              Buckets: \d+  Batches: \d+  Memory Usage: \d+kB)?
              ->  Seq Scan on bar \(Current loop: actual rows=\d+, loop number=1\)"""

	qs = query_state(config, query, {'format': 'text'})
	debug_output(qs, 1, None, query, expected)
	assert 	len(qs) == 1 and re.match(expected, qs[0][3])

	qs = query_state(config, query, {'format': 'json'})
	try:
		js_obj = json.loads(qs[0][3])
	except ValueError:
		assert False, 'Invalid json format'
	assert	len(qs) == 1
	check_plan(js_obj['Plan'])

	qs = query_state(config, query, {'format': 'xml'})
	assert 	len(qs) == 1
	try:
		xml_root = ET.fromstring(qs[0][3])
	except:
		assert False, 'Invalid xml format'
	check_xml(xml_root)

	qs = query_state(config, query, {'format': 'yaml'})
	try:
		yaml_doc = yaml.load(qs[0][3])
	except:
		assert False, 'Invalid yaml format'
	assert 	len(qs) == 1
	check_plan(yaml_doc['Plan'])

def test_timing_buffers_conflicts(node):
	"""test when caller requests timing and buffers but counterpart turned off its"""

	query = 'select count(*) from foo join bar on foo.c1=bar.c1'
	timing_pattern = '(?:running time=\d+.\d+)|(?:actual time=\d+.\d+..\d+.\d+)'
	buffers_pattern = 'Buffers:'

	qs = query_state(node, query, {'timing': True, 'buffers': False})
	assert 	len(qs) == 1 and not re.search(timing_pattern, qs[0][3])
	# Here you can add a check of notices in the case when the 
	# testgres will be able to receive them
	#assert 'WARNING:  timing statistics disabled\n' in notices

	qs = query_state(node, query, {'timing': False, 'buffers': True})
	assert 	len(qs) == 1 and not re.search(buffers_pattern, qs[0][3])
	#assert 'WARNING:  buffers statistics disabled\n' in notices

	qs = query_state(node, query, {'timing': True, 'buffers': True})
	assert 	len(qs) == 1 and not re.search(timing_pattern, qs[0][3]) \
						 and not re.search(buffers_pattern, qs[0][3])
	#assert len(notices) == 2 and 'WARNING:  timing statistics disabled\n' in notices \
	#						 and 'WARNING:  buffers statistics disabled\n' in notices


class DataLoadException(Exception): pass
class StressTestException(Exception): pass

def load_tpcds_data(node):
	print 'Load tpcds...'
	subprocess.call(['./tests/prepare_stress.sh'])
	try:
		# Create tables
		node.psql(filename="tmp_stress/tpcds-kit/tools/tpcds.sql")
		# Copy table data from files
		for table_datafile in os.listdir('tmp_stress/tpcds-kit/tools/'):
			if table_datafile.endswith(".dat"):
				table_name = os.path.splitext(os.path.basename(table_datafile))[0]
				copy_cmd = "\\copy %s FROM 'tmp_stress/tpcds-kit/tools/tables/%s' CSV DELIMITER '|'" % (table_name, table_datafile)
				print "Load table ", table_name 
				node.safe_psql("TRUNCATE %s" % table_name)
				node.safe_psql(copy_cmd)
	except Exception, e:
		raise DataLoadException('Load failed: %s' % e)
	print 'done!'

def stress_test(node):
	"""stress test"""
	load_tpcds_data(node)
	print 'Test running...'
	# execute query in separate thread 
	with open("tests/query_tpcds.sql",'r') as f:
		sql = f.read()
	commands = sql.split(';')
	for i, cmd in enumerate(sql.split(';')):
		if (len(cmd.strip()) == 0):
			del commands[i]

	timeout_list = []
	bar = progressbar.ProgressBar(max_value=len(commands))
	for i, cmd in enumerate(commands):
		bar.update(i+1)
		try:
			conn = testgres.connection.NodeConnection(node)
			aq = AsyncQueryExecutor(node)
			# set query timeout to 10 sec 
			set_guc(aq.conn, 'statement_timeout', 10000)
			set_guc(conn, 'statement_timeout', 10000)
			aq.run(cmd, True)
			while aq.process.is_alive() and node.pid:
				conn.execute('SELECT * FROM pg_query_state(%d)' % aq.backend_pid)
		#TODO: Put here testgres exception when supported
		except psycopg2.extensions.QueryCanceledError:
			timeout_list.append(i)
		finally:
			aq.close()
			conn.close()
		if len(timeout_list) > 0:
			print 'It was pg_query_state timeouts(10s) on queries: ', timeout_list
