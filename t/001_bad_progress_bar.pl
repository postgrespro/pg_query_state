# pg_query_state/t/test_bad_progress_bar.pl
#
# Check uncorrect launches of functions pg_progress_bar(pid)
# and pg_progress_bar_visual(pid, delay)

use strict;
use warnings;
use Test::More tests => 4;

# List of checks for bad cases:
#     1) appealing to a bad pid
#  ------- requires DBI and DBD::Pg modules -------
#     2) extracting the state of the process itself

my $node;

# modules depend on the PostgreSQL version
my $pg_15_modules;

BEGIN
{
	$pg_15_modules = eval
	{
		require PostgreSQL::Test::Cluster;
		require PostgreSQL::Test::Utils;
		return 1;
	};

	unless (defined $pg_15_modules)
	{
		$pg_15_modules = 0;

		require PostgresNode;
		require TestLib;
	}
}

note('PostgreSQL 15 modules are used: ' . ($pg_15_modules ? 'yes' : 'no'));

if ($pg_15_modules)
{
	$node = PostgreSQL::Test::Cluster->new("master");
}
else
{
	$node = PostgresNode->get_new_node("master");
}

# start backend for function pg_progress_bar
my $dbh_status;
my $pid_status;

# this code exists only because of problems
# with authentification in Windows
# 
# in a friendlier system it would be enough to do:
#
# $node->init;
# $node->start;
# $node->append_conf('postgresql.conf', "shared_preload_libraries = 'pg_query_state'");
# $node->restart;
# $node->psql('postgres', 'CREATE EXTENSION pg_query_state;');
#
# but now we will carefully configure the work
# for specific users and databases
# -----------------------------------------------------------
$ENV{LC_ALL} = 'C';
$ENV{PGCLIENTENCODING} = 'LATIN1';

my $dbname1 = 'regression_bad_progress_bar';
my $username1 = $dbname1;
my $src_bootstrap_super = 'regress_postgres';

$node->init(
	extra => [
		'--username' => $src_bootstrap_super,
		'--locale' => 'C',
		'--encoding' => 'LATIN1',
	]);

# update pg_hba.conf and pg_ident.conf
# for sppi-authentification in Windows
$node->run_log(
	[
		$ENV{PG_REGRESS},
		'--config-auth' => $node->data_dir,
		'--user' => $src_bootstrap_super,
		'--create-role' => "$username1",
	]);

$node->start;

# create test user and test database
$node->run_log(
	[ 'createdb', '--username' => $src_bootstrap_super, $dbname1 ]);
$node->run_log(
	[
		'createuser',
		'--username' => $src_bootstrap_super,
		'--superuser',
		$username1,
	]);

$node->append_conf('postgresql.conf', "shared_preload_libraries = 'pg_query_state'");
$node->restart;

# now we are ready to create extension pg_query_state
# we perform this and following actions under the
# created test user and on the test database
$node->psql($dbname1, 'CREATE EXTENSION pg_query_state;',
			extra_params => ['-U', $username1]);
# -----------------------------------------------------------

sub bad_pid
{
	note('Extracting from bad pid');
	my $stderr;
	$node->psql($dbname1, 'SELECT * from pg_progress_bar(-1)',
			stderr => \$stderr, extra_params => ['-U', $username1]);
	is ($stderr, 'psql:<stdin>:1: ERROR:  backend with pid=-1 not found',
			"appealing to a bad pid for pg_progress_bar");
	$node->psql($dbname1, 'SELECT * from pg_progress_bar(-1)_visual',
			stderr => \$stderr, extra_params => ['-U', $username1]);
	is ($stderr, 'psql:<stdin>:1: ERROR:  backend with pid=-1 not found',
			"appealing to a bad pid for pg_progress_bar_visual");
}

sub self_status
{
	note('Extracting your own status');
	$dbh_status->do('SELECT * from pg_progress_bar(' . $pid_status . ')');
	is($dbh_status->errstr, 'ERROR:  attempt to extract state of current process',
			"extracting the state of the process itself for pg_progress_bar");
	$dbh_status->do('SELECT * from pg_progress_bar_visual(' . $pid_status . ')');
	is($dbh_status->errstr, 'ERROR:  attempt to extract state of current process',
			"extracting the state of the process itself for pg_progress_bar_visual");
}

# 2 tests for 1 case
bad_pid();

# Check whether we have both DBI and DBD::pg

my $dbdpg_rc = eval
{
	require DBI;
	require DBD::Pg;
	1;
};

$dbdpg_rc = 0 unless defined $dbdpg_rc;

if ($dbdpg_rc != 1)
{
	diag('DBI and DBD::Pg are not available, skip 2/4 tests');
}

SKIP: {
	skip "DBI and DBD::Pg are not available", 2 if ($dbdpg_rc != 1);

	DBD::Pg->import(':async');

	# connect to test database under the test user 
	$dbh_status = DBI->connect('DBI:Pg:' . $node->connstr() . " user=$username1" . " dbname=$dbname1");
	if ( !defined $dbh_status )
	{
		die "Cannot connect to database for dbh with pg_progress_bar\n";
	}

	$pid_status = $dbh_status->{pg_pid};

	# 2 tests for 2 case
	self_status();

	$dbh_status->disconnect;
}

$node->stop('fast');

