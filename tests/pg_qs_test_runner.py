'''
pg_qs_test_cases.py
				Tests extract query state from running backend (including concurrent extracts)
Copyright (c) 2016-2016, Postgres Professional
'''
from __future__ import print_function, division, absolute_import

import os
import sys
import argparse
import getpass
import psycopg2

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from test_cases import *

class PasswordPromptAction(argparse.Action):
	def __call__(self, parser, args, values, option_string=None):
		password = getpass.getpass()
		setattr(args, self.dest, password)

class SetupException(Exception): pass
class TeardownException(Exception): pass

setup_cmd = [
	'drop extension if exists pg_query_state cascade',
	'drop table if exists foo cascade',
	'drop table if exists bar cascade',
	'create extension pg_query_state',
	'create table foo(c1 integer, c2 text)',
	'create table bar(c1 integer, c2 boolean)',
	'insert into foo select i, md5(random()::text) from generate_series(1, 1000000) as i',
	'insert into bar select i, i%2=1 from generate_series(1, 500000) as i',
	'analyze foo',
	'analyze bar',
	]

teardown_cmd = [
	'drop table foo cascade',
	'drop table bar cascade',
	'drop extension pg_query_state cascade',
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

def setup(con):
	''' Creates pg_query_state extension, creates tables for tests, fills it with data '''
	print('setting up...')
	try:
		cur = con.cursor()
		for cmd in setup_cmd:
			cur.execute(cmd)
		con.commit()
		cur.close()
	except Exception as e:
		raise SetupException('Setup failed: %s' % e)
	print('done!')

def teardown(con):
	''' Drops table and extension '''
	print('tearing down...')
	try:
		cur = con.cursor()
		for cmd in teardown_cmd:
			cur.execute(cmd)
		con.commit()
		cur.close()
	except Exception as e:
		raise TeardownException('Teardown failed: %s' % e)
	print('done!')

def main(config):
	''' Main test function '''

	con = psycopg2.connect(**config)
	setup(con)

	for i, test in enumerate(tests):
		if test.__doc__:
			descr = test.__doc__
		else:
			descr = 'test case %d' % (i+1)
		print(("%s..." % descr))
		sys.stdout.flush()
		test(config)
		print('ok!')

	if os.environ['LEVEL'] == 'stress':
		print('Starting stress test')
		test_tpc_ds(config)
		print('Stress finished successfully')

	teardown(con)
	con.close()

if __name__ == '__main__':
	parser = argparse.ArgumentParser(description='Query state of running backends tests')

	parser.add_argument('--host', default='localhost', help='postgres server host')
	parser.add_argument('--port', type=int, default=5432, help='postgres server port')
	parser.add_argument('--user', dest='user', default='postgres', help='user name')
	parser.add_argument('--database', dest='database', default='postgres', help='database name')
	parser.add_argument('--password', dest='password', nargs=0, action=PasswordPromptAction, default='')

	args = parser.parse_args()
	main(args.__dict__)
