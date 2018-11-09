'''
pg_qs_test_cases.py
				Tests extract query state from running backend (including concurrent extracts)
Copyright (c) 2016-2016, Postgres Professional
'''

import argparse
import sys
from test_cases import *
import testgres

class SetupException(Exception): pass

setup_cmd = [
	'drop extension if exists pg_query_state cascade',
	'drop table if exists foo cascade',
	'drop table if exists bar cascade',
	'create extension pg_query_state',
	'create table foo(c1 integer, c2 text)',
	'create table bar(c1 integer, c2 boolean)',
	'insert into foo select i, md5(random()::text) from generate_series(1, 1000000) as i',
	'insert into bar select i, i%%2=1 from generate_series(1, 500000) as i',
	'analyze foo',
	'analyze bar',
	]

tests = [
        test_deadlock,
        test_simple_query,
        test_concurrent_access,
        test_nested_call,
        test_trigger,
        test_costs,
        test_buffers,
        test_timing,
        test_formats,
        test_timing_buffers_conflicts,
        test_insert_on_conflict,
        ]

def setup(node):
	''' Creates pg_query_state extension, creates tables for tests, fills it with data '''
	print 'setting up...'
	conn = testgres.connection.NodeConnection(node)
	try:
		for cmd in setup_cmd:
			conn.execute(cmd)
		conn.commit()
		conn.close()
	except Exception, e:
		raise SetupException('Setup failed: %s' % e)
	print 'done!'

def main(args):
	''' Main test function '''
	node = testgres.get_new_node()
	node.init()
	node.append_conf("shared_preload_libraries='pg_query_state'\n")
	node.start()
	setup(node)

	for i, test in enumerate(tests):
		if test.__doc__:
			descr = test.__doc__
		else:
			descr = 'test case %d' % (i+1)
		print ("%s..." % descr),; sys.stdout.flush()
		test(node)
		print 'ok!'

	if args.stress:
		print 'Start stress test'
		stress_test(node)
		print 'Start stress finished successfully'
	print 'stop'

	node.stop()
	node.cleanup()

if __name__ == '__main__':
	parser = argparse.ArgumentParser(description='Query state of running backends tests')
	parser.add_argument('--stress', help='run stress test using tpc-ds benchmark',
						action="store_true")
	args = parser.parse_args()
	main(args)
