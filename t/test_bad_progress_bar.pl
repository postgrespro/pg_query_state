# pg_query_state/t/test_bad_progress_bar.pl
#
# Check uncorrect launches of functions progress_bar(pid)
# and progress_bar_visual(pid, delay)

use strict;
use warnings;
use PostgresNode;
use TestLib;
use Test::More tests => 2;

# List of checks for bad cases:
#     1) appealing to a bad pid
#  ------- requires DBI and DBD::Pg modules -------
#     2) extracting the state of the process itself

# Test whether we have both DBI and DBD::pg
my $dbdpg_rc = eval
{
  require DBI;
  require DBD::Pg;
  DBD::Pg->import(':async');
  1;
};

# start backend for function progress_bar
my $node = PostgresNode->get_new_node('master');
$node->init;
$node->start;
$node->append_conf('postgresql.conf', "shared_preload_libraries = 'pg_query_state'");
$node->restart;
$node->psql('postgres', 'CREATE EXTENSION pg_query_state;');

subtest 'Extracting from bad pid' => sub {
        my $stderr;
        $node->psql('postgres', 'SELECT * from progress_bar(-1)', stderr => \$stderr);
        is ($stderr, 'psql:<stdin>:1: ERROR:  backend with pid=-1 not found', "appealing to a bad pid for progress_bar");
        $node->psql('postgres', 'SELECT * from progress_bar(-1)_visual', stderr => \$stderr);
        is ($stderr, 'psql:<stdin>:1: ERROR:  backend with pid=-1 not found', "appealing to a bad pid for progress_bar_visual");
};

if ( not $dbdpg_rc) {
        diag('DBI and DBD::Pg are not available, skip 2/3 tests');
}

SKIP: {
        skip "DBI and DBD::Pg are not available", 2 if not $dbdpg_rc;

        my $dbh_status = DBI->connect('DBI:Pg:' . $node->connstr($_));
        if ( !defined $dbh_status )
        {
                die "Cannot connect to database for dbh with progress_bar\n";
        } 

        my $pid_status = $dbh_status->{pg_pid};

        subtest 'Extracting your own status' => sub {
                $dbh_status->do('SELECT * from progress_bar(' . $pid_status . ')');
                is($dbh_status->errstr, 'ERROR:  attempt to extract state of current process', "extracting the state of the process itself for progress_bar");
                $dbh_status->do('SELECT * from progress_bar_visual(' . $pid_status . ')');
                is($dbh_status->errstr, 'ERROR:  attempt to extract state of current process', "extracting the state of the process itself for progress_bar_visual");
        };

        $dbh_status->disconnect;
}

$node->stop('fast');
