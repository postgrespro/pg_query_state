[![Build Status](https://travis-ci.com/postgrespro/pg_query_state.svg?branch=master)](https://travis-ci.com/postgrespro/pg_query_state)
[![codecov](https://codecov.io/gh/postgrespro/pg_query_state/branch/master/graph/badge.svg)](https://codecov.io/gh/postgrespro/pg_query_state)

# pg\_query\_state
The `pg_query_state` module provides facility to know the current state of query execution on working backend. To enable this extension you have to patch the stable version of PostgreSQL, recompile it and deploy new binaries. All patch files are located in `patches/` directory and tagged with suffix of PostgreSQL version number.

## Overview
Each nonutility query statement (SELECT/INSERT/UPDATE/DELETE) after optimization/planning stage is translated into plan tree which is kind of imperative representation of SQL query execution algorithm. EXPLAIN ANALYZE request allows to demonstrate execution statistics gathered from each node of plan tree (full time of execution, number rows emitted to upper nodes, etc). But this statistics is collected after execution of query. This module allows to show actual statistics of query running gathered from external backend. At that, format of resulting output is almost identical to ordinal EXPLAIN ANALYZE. Thus users are able to track of query execution in progress.

In fact, this module is able to explore external backend and determine its actual state. Particularly it's helpful when backend executes a heavy query and gets stuck.

## Use cases
Using this module there can help in the following things:
 - detect a long query (along with other monitoring tools)
 - overwatch the query execution

## Installation
To install `pg_query_state`, please apply corresponding patches `custom_signal_(PG_VERSION).patch` and `runtime_explain_(PG_VERSION).patch` (or `runtime_explain.patch` for PG version <= 10.0) from the `patches/` directory to reqired stable version of PostgreSQL and rebuild PostgreSQL.

To do this, run the following commands from the postgresql directory:
```
patch -p1 < path_to_pg_query_state_folder/patches/runtime_explain_(PG_VERSION).patch
patch -p1 < path_to_pg_query_state_folder/patches/custom_signals_(PG_VERSION).patch
```

Then execute this in the module's directory:
```
make install USE_PGXS=1
```
To execute the command correctly, make sure you have the PATH or PG_CONFIG variable set.
```
export PATH=path_to_your_bin_folder:$PATH
# or
export PG_CONFIG=path_to_your_bin_folder/pg_config
```

Add module name to the `shared_preload_libraries` parameter in `postgresql.conf`:
```
shared_preload_libraries = 'pg_query_state'
```
It is essential to restart the PostgreSQL instance. After that, execute the following query in psql:
```sql
CREATE EXTENSION pg_query_state;
```
Done!

## Tests
Test using parallel sessions with Python 3+ compatible script:
```shell
python3 tests/pg_qs_test_runner.py [OPTION]...
```
*prerequisite packages*:
* `psycopg2` version 2.6 or later
* `PyYAML` version 3.11 or later
* `progressbar2` for stress test progress reporting

*options*:
* *- -host* --- postgres server host, default value is *localhost*
* *- -port* --- postgres server port, default value is *5432*
* *- -database* --- database name, default value is *postgres*
* *- -user* --- user name, default value is *postgres*
* *- -password* --- user's password, default value is empty
* *- -tpc-ds-setup* --- setup database to run TPC-DS benchmark
* *- -tpc-ds-run* --- runs only stress tests on TPC-DS benchmark

Or run all tests in `Docker` using:

```shell
export LEVEL=hardcore
export USE_TPCDS=1
export PG_VERSION=12

./mk_dockerfile.sh

docker-compose build
docker-compose run tests
```

There are different test levels: `hardcore`, `nightmare` (runs tests under `valgrind`) and `stress` (runs tests under `TPC-DS` load).

## Function pg\_query\_state
```plpgsql
pg_query_state(
        integer     pid,
        verbose     boolean DEFAULT FALSE,
        costs       boolean DEFAULT FALSE,
        timing      boolean DEFAULT FALSE,
        buffers     boolean DEFAULT FALSE,
        triggers    boolean DEFAULT FALSE,
        format      text    DEFAULT 'text'
) returns TABLE (
    pid             integer,
    frame_number    integer,
    query_text      text,
    plan            text,
    leader_pid      integer
)
```
extracts the current query state from backend with specified `pid`. Since parallel query can spawn multiple workers and function call causes nested subqueries so that state of execution may be viewed as stack of running queries, return value of `pg_query_state` has type `TABLE (pid integer, frame_number integer, query_text text, plan text, leader_pid integer)`. It represents tree structure consisting of leader process and its spawned workers identified by `pid`. Each worker refers to leader through `leader_pid` column. For leader process the value of this column is` null`. The state of each process is represented as stack of function calls. Each frame of that stack is specified as correspondence between `frame_number` starting from zero, `query_text` and `plan` with online statistics columns.

Thus, user can see the states of main query and queries generated from function calls for leader process and all workers spawned from it.

In process of execution some nodes of plan tree can take loops of full execution. Therefore statistics for each node consists of two parts: average statistics for previous loops just like in EXPLAIN ANALYZE output and statistics for current loop if node have not finished.

Optional arguments:

 - `verbose` --- use EXPLAIN VERBOSE for plan printing;
 - `costs` --- add costs for each node;
 - `timing` --- print timing data for each node, if collecting of timing statistics is turned off on called side resulting output will contain WARNING message `timing statistics disabled`;
 - `buffers` --- print buffers usage, if collecting of buffers statistics is turned off on called side resulting output will contain WARNING message `buffers statistics disabled`;
 - `triggers` --- include triggers statistics in result plan trees;
 - `format` --- EXPLAIN format to be used for plans printing, possible values: {`text`, `xml`, `json`, `yaml`}.

If callable backend is not executing any query the function prints INFO message about backend's state taken from `pg_stat_activity` view if it exists there.

**_Warning_**: Calling role have to be superuser or member of the role whose backend is being called. Otherwise function prints ERROR message `permission denied`.

## Configuration settings
There are several user-accessible [GUC](https://www.postgresql.org/docs/9.5/static/config-setting.html) variables designed to toggle the whole module and the collecting of specific statistic parameters while query is running:

 - `pg_query_state.enable` --- disable (or enable) `pg_query_state` completely, default value is `true`
 - `pg_query_state.enable_timing` --- collect timing data for each node, default value is `false`
 - `pg_query_state.enable_buffers` --- collect buffers usage, default value is `false`

This parameters is set on called side before running any queries whose states are attempted to extract. **_Warning_**: if `pg_query_state.enable_timing` is turned off the calling side cannot get time statistics, similarly for `pg_query_state.enable_buffers` parameter.

## Examples
Set maximum number of parallel workers on `gather` node equals `2`:
```sql
postgres=# set max_parallel_workers_per_gather = 2;
```
Assume one backend with pid = 49265 performs a simple query:
```sql
postgres=# select pg_backend_pid();
 pg_backend_pid
 ----------------
          49265
(1 row)
postgres=# select count(*) from foo join bar on foo.c1=bar.c1;
```
Other backend can extract intermediate state of execution that query:
```sql
postgres=# \x
postgres=# select * from pg_query_state(49265);
-[ RECORD 1 ]+-------------------------------------------------------------------------------------------------------------------------
pid          | 49265
frame_number | 0
query_text   | select count(*) from foo join bar on foo.c1=bar.c1;
plan         | Finalize Aggregate (Current loop: actual rows=0, loop number=1)                                                         +
             |   ->  Gather (Current loop: actual rows=0, loop number=1)                                                               +
             |         Workers Planned: 2                                                                                              +
             |         Workers Launched: 2                                                                                             +
             |         ->  Partial Aggregate (Current loop: actual rows=0, loop number=1)                                              +
             |               ->  Nested Loop (Current loop: actual rows=12, loop number=1)                                             +
             |                     Join Filter: (foo.c1 = bar.c1)                                                                      +
             |                     Rows Removed by Join Filter: 5673232                                                                +
             |                     ->  Parallel Seq Scan on foo (Current loop: actual rows=12, loop number=1)                          +
             |                     ->  Seq Scan on bar (actual rows=500000 loops=11) (Current loop: actual rows=173244, loop number=12)
leader_pid   | (null)
-[ RECORD 2 ]+-------------------------------------------------------------------------------------------------------------------------
pid          | 49324
frame_number | 0
query_text   | <parallel query>
plan         | Partial Aggregate (Current loop: actual rows=0, loop number=1)                                                          +
             |   ->  Nested Loop (Current loop: actual rows=10, loop number=1)                                                         +
             |         Join Filter: (foo.c1 = bar.c1)                                                                                  +
             |         Rows Removed by Join Filter: 4896779                                                                            +
             |         ->  Parallel Seq Scan on foo (Current loop: actual rows=10, loop number=1)                                      +
             |         ->  Seq Scan on bar (actual rows=500000 loops=9) (Current loop: actual rows=396789, loop number=10)
leader_pid   | 49265
-[ RECORD 3 ]+-------------------------------------------------------------------------------------------------------------------------
pid          | 49323
frame_number | 0
query_text   | <parallel query>
plan         | Partial Aggregate (Current loop: actual rows=0, loop number=1)                                                          +
             |   ->  Nested Loop (Current loop: actual rows=11, loop number=1)                                                         +
             |         Join Filter: (foo.c1 = bar.c1)                                                                                  +
             |         Rows Removed by Join Filter: 5268783                                                                            +
             |         ->  Parallel Seq Scan on foo (Current loop: actual rows=11, loop number=1)                                      +
             |         ->  Seq Scan on bar (actual rows=500000 loops=10) (Current loop: actual rows=268794, loop number=11)
leader_pid   | 49265
```
In example above working backend spawns two parallel workers with pids `49324` and `49323`. Their `leader_pid` column's values clarify that these workers belong to the main backend.
`Seq Scan` node has statistics on passed loops (average number of rows delivered to `Nested Loop` and number of passed loops are shown) and statistics on current loop. Other nodes has statistics only for current loop as this loop is first (`loop number` = 1).

Assume first backend executes some function:
```sql
postgres=# select n_join_foo_bar();
```
Other backend can get the follow output:
```sql
postgres=# select * from pg_query_state(49265);
-[ RECORD 1 ]+------------------------------------------------------------------------------------------------------------------
pid          | 49265
frame_number | 0
query_text   | select n_join_foo_bar();
plan         | Result (Current loop: actual rows=0, loop number=1)
leader_pid   | (null)
-[ RECORD 2 ]+------------------------------------------------------------------------------------------------------------------
pid          | 49265
frame_number | 1
query_text   | SELECT (select count(*) from foo join bar on foo.c1=bar.c1)
plan         | Result (Current loop: actual rows=0, loop number=1)                                                              +
             |   InitPlan 1 (returns $0)                                                                                        +
             |     ->  Aggregate (Current loop: actual rows=0, loop number=1)                                                   +
             |           ->  Nested Loop (Current loop: actual rows=51, loop number=1)                                          +
             |                 Join Filter: (foo.c1 = bar.c1)                                                                   +
             |                 Rows Removed by Join Filter: 51636304                                                            +
             |                 ->  Seq Scan on bar (Current loop: actual rows=52, loop number=1)                                +
             |                 ->  Materialize (actual rows=1000000 loops=51) (Current loop: actual rows=636355, loop number=52)+
             |                       ->  Seq Scan on foo (Current loop: actual rows=1000000, loop number=1)
leader_pid   | (null)
```
First row corresponds to function call, second - to query which is in the body of that function.

We can get result plans in different format (e.g. `json`):
```sql
postgres=# select * from pg_query_state(pid := 49265, format := 'json');
-[ RECORD 1 ]+------------------------------------------------------------
pid          | 49265
frame_number | 0
query_text   | select * from n_join_foo_bar();
plan         | {                                                          +
             |   "Plan": {                                                +
             |     "Node Type": "Function Scan",                          +
             |     "Parallel Aware": false,                               +
             |     "Function Name": "n_join_foo_bar",                     +
             |     "Alias": "n_join_foo_bar",                             +
             |     "Current loop": {                                      +
             |       "Actual Loop Number": 1,                             +
             |       "Actual Rows": 0                                     +
             |     }                                                      +
             |   }                                                        +
             | }
leader_pid   | (null)
-[ RECORD 2 ]+------------------------------------------------------------
pid          | 49265
frame_number | 1
query_text   | SELECT (select count(*) from foo join bar on foo.c1=bar.c1)
plan         | {                                                          +
             |   "Plan": {                                                +
             |     "Node Type": "Result",                                 +
             |     "Parallel Aware": false,                               +
             |     "Current loop": {                                      +
             |       "Actual Loop Number": 1,                             +
             |       "Actual Rows": 0                                     +
             |     },                                                     +
             |     "Plans": [                                             +
             |       {                                                    +
             |         "Node Type": "Aggregate",                          +
             |         "Strategy": "Plain",                               +
             |         "Partial Mode": "Simple",                          +
             |         "Parent Relationship": "InitPlan",                 +
             |         "Subplan Name": "InitPlan 1 (returns $0)",         +
             |         "Parallel Aware": false,                           +
             |         "Current loop": {                                  +
             |           "Actual Loop Number": 1,                         +
             |           "Actual Rows": 0                                 +
             |         },                                                 +
             |         "Plans": [                                         +
             |           {                                                +
             |             "Node Type": "Nested Loop",                    +
             |             "Parent Relationship": "Outer",                +
             |             "Parallel Aware": false,                       +
             |             "Join Type": "Inner",                          +
             |             "Current loop": {                              +
             |               "Actual Loop Number": 1,                     +
             |               "Actual Rows": 610                           +
             |             },                                             +
             |             "Join Filter": "(foo.c1 = bar.c1)",            +
             |             "Rows Removed by Join Filter": 610072944,      +
             |             "Plans": [                                     +
             |               {                                            +
             |                 "Node Type": "Seq Scan",                   +
             |                 "Parent Relationship": "Outer",            +
             |                 "Parallel Aware": false,                   +
             |                 "Relation Name": "bar",                    +
             |                 "Alias": "bar",                            +
             |                 "Current loop": {                          +
             |                   "Actual Loop Number": 1,                 +
             |                   "Actual Rows": 611                       +
             |                 }                                          +
             |               },                                           +
             |               {                                            +
             |                 "Node Type": "Materialize",                +
             |                 "Parent Relationship": "Inner",            +
             |                 "Parallel Aware": false,                   +
             |                 "Actual Rows": 1000000,                    +
             |                 "Actual Loops": 610,                       +
             |                 "Current loop": {                          +
             |                   "Actual Loop Number": 611,               +
             |                   "Actual Rows": 73554                     +
             |                 },                                         +
             |                 "Plans": [                                 +
             |                   {                                        +
             |                     "Node Type": "Seq Scan",               +
             |                     "Parent Relationship": "Outer",        +
             |                     "Parallel Aware": false,               +
             |                     "Relation Name": "foo",                +
             |                     "Alias": "foo",                        +
             |                     "Current loop": {                      +
             |                       "Actual Loop Number": 1,             +
             |                       "Actual Rows": 1000000               +
             |                     }                                      +
             |                   }                                        +
             |                 ]                                          +
             |               }                                            +
             |             ]                                              +
             |           }                                                +
             |         ]                                                  +
             |       }                                                    +
             |     ]                                                      +
             |   }                                                        +
             | }
leader_pid   | (null)
```

## Feedback
Do not hesitate to post your issues, questions and new ideas at the [issues](https://github.com/postgrespro/pg_query_state/issues) page.

## Authors
[Maksim Milyutin](https://github.com/maksm90)  
Alexey Kondratov <a.kondratov@postgrespro.ru> Postgres Professional Ltd., Russia

## Function progress\_bar
```plpgsql
pg_progress_bar(
        integer     pid
) returns FLOAT
```
extracts the current query state from backend with specified 'pid'. Then gets the numerical values of the actual rows and total rows and count progress for the whole query tree. Function returns numeric value from 0 to 1 describing the measure of query fulfillment. If there is no information about current state of the query, or the impossibility of counting, the corresponding messages will be displayed.

## Function progress\_bar\_visual
```plpgsql
pg_progress_bar_visual(
        integer     pid,
        integer     delay
) returns VOID
```
cyclically extracts and print the current query state in numeric value from backend with specified 'pid' every period specified by 'delay' in seconds. This is the looping version of the progress\_bar function that returns void value.

**_Warning_**: Calling role have to be superuser or member of the role whose backend is being called. Otherwise function prints ERROR message `permission denied`.

## Examples
Assume first backend executes some function:
```sql
postgres=# insert into table_name select generate_series(1,10000000);
```
Other backend can get the follow output:
```sql
postgres=# SELECT pid FROM pg_stat_activity where query like 'insert%';
  pid
-------
 23877
(1 row)

postgres=# SELECT pg_progress_bar(23877);
 pg_progress_bar
-----------------
       0.6087927
(1 row)
```
Or continuous version:
```sql
postgres=# SELECT pg_progress_bar_visual(23877, 1);
Progress = 0.043510
Progress = 0.085242
Progress = 0.124921
Progress = 0.168168
Progress = 0.213803
Progress = 0.250362
Progress = 0.292632
Progress = 0.331454
Progress = 0.367509
Progress = 0.407450
Progress = 0.448646
Progress = 0.488171
Progress = 0.530559
Progress = 0.565558
Progress = 0.608039
Progress = 0.645778
Progress = 0.654842
Progress = 0.699006
Progress = 0.735760
Progress = 0.787641
Progress = 0.832160
Progress = 0.871077
Progress = 0.911858
Progress = 0.956362
Progress = 0.995097
Progress = 1.000000
 pg_progress_bar_visual
------------------------
                      1
(1 row)
```
Also uncountable queries exist. Assume first backend executes some function:
```sql
DELETE from table_name;
```
Other backend can get the follow output:
```sql
postgres=# SELECT pid FROM pg_stat_activity where query like 'delete%';
  pid
-------
 23877
(1 row)

postgres=# SELECT pg_progress_bar(23877);
INFO:  Counting Progress doesn't available
 pg_progress_bar
-----------------
              -1
(1 row)

postgres=# SELECT pg_progress_bar_visual(23877, 5);
INFO:  Counting Progress doesn't available
 pg_progress_bar_visual
------------------------
                      -1
(1 row)
```

## Reinstallation
If you already have a module 'pg_query_state' without progress bar functions installed, execute this in the module's directory:
```
make install USE_PGXS=1
```
It is essential to restart the PostgreSQL instance. After that, execute the following queries in psql:
```sql
DROP EXTENSION IF EXISTS pg_query_state;
CREATE EXTENSION pg_query_state;
```

## Authors
Ekaterina Sokolova <e.sokolova@postgrespro.ru> Postgres Professional Ltd., Russia
Vyacheslav Makarov <v.makarov@postgrespro.ru> Postgres Professional Ltd., Russia
