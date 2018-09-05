# pg\_query\_state
The `pg_query_state` module provides facility to know the current state of query execution on working backend. To enable this extension you have to patch the latest stable version of PostgreSQL. Different branches are intended for different version numbers of PostgreSQL, e.g., branch _PG9_5_ corresponds to PostgreSQL 9.5.

## Overview
Each nonutility query statement (SELECT/INSERT/UPDATE/DELETE) after optimization/planning stage is translated into plan tree wich is kind of imperative representation of declarative SQL query. EXPLAIN ANALYZE request allows to demonstrate execution statistics gathered from each node of plan tree (full time of execution, number rows emitted to upper nodes, etc). But this statistics is collected after execution of query. This module allows to show actual statistics of query running on external backend. At that, format of resulting output is almost identical to ordinal EXPLAIN ANALYZE. Thus users are able to track of query execution in progress.

In fact, this module is able to explore external backend and determine its actual state. Particularly it's helpful when backend executes a heavy query or gets stuck.

## Use cases
Using this module there can help in the following things:
 - detect a long query (along with other monitoring tools)
 - overwatch the query execution

## Installation
To install `pg_query_state`, please apply patches `custom_signal.patch` and `runtime_explain.patch` to the latest stable version of PostgreSQL and rebuild PostgreSQL.

Correspondence branch names to PostgreSQL version numbers:
- _PG9_5_ --- PostgreSQL 9.5
- _PGPRO9_5_ --- PostgresPro 9.5
- _PGPRO9_6_ --- PostgreSQL 9.6 and PostgresPro 9.6
- _PGPRO10_ --- PostgresPro 10
- _PG10_ --- PostgreSQL 10
- _PG11_ --- PostgreSQL 11
- _master_ --- development version for the newest version PostgreSQL

Then execute this in the module's directory:
```
make install USE_PGXS=1
```
Add module name to the `shared_preload_libraries` parameter in `postgresql.conf`:
```
shared_preload_libraries = 'pg_query_state'
```
It is essential to restart the PostgreSQL instance. After that, execute the following query in psql:
```
CREATE EXTENSION pg_query_state;
```
Done!

## Tests
Tests using parallel sessions using python 2.7 script:
   ```
   python tests/pg_qs_test_runner.py [OPTION]...
   ```
*prerequisite packages*:
* `psycopg2` version 2.6 or later
* `PyYAML` version 3.11 or later
   
*options*:
* *- -host* --- postgres server host, default value is *localhost*
* *- -port* --- postgres server port, default value is *5432*
* *- -database* --- database name, default value is *postgres*
* *- -user* --- user name, default value is *postgres*
* *- -password* --- user's password, default value is empty

## Function pg\_query\_state
```plpgsql
pg_query_state(integer 	pid,
			   verbose	boolean DEFAULT FALSE,
			   costs 	boolean DEFAULT FALSE,
			   timing 	boolean DEFAULT FALSE,
			   buffers 	boolean DEFAULT FALSE,
			   triggers	boolean DEFAULT FALSE,
			   format	text 	DEFAULT 'text')
	returns TABLE ( pid integer,
	                frame_number integer,
	                query_text text,
	                plan text,
	                leader_pid integer)
```
Extract current query state from backend with specified `pid`. Since parallel query can spawn multiple workers and function call causes nested subqueries so that state of execution may be viewed as stack of running queries, return value of `pg_query_state` has type `TABLE (pid integer, frame_number integer, query_text text, plan text, leader_pid integer)`. It represents tree structure consisting of leader process and its spawned workers identified by `pid`. Each worker refers to leader through `leader_pid` column. For leader process the value of this column is` null`. The state of each process is represented as stack of function calls. Each frame of that stack is specified as correspondence between `frame_number` starting from zero, `query_text` and `plan` with online statistics columns.

Thus, user can see the states of main query and queries generated from function calls for leader process and all workers spawned from it.

In process of execution some nodes of plan tree can take loops of full execution. Therefore statistics for each node consists of two parts: average statistics for previous loops just like in EXPLAIN ANALYZE output and statistics for current loop if node have not finished.

Optional arguments:

 - `verbose` --- use EXPLAIN VERBOSE for plan printing;
 - `costs` --- add costs for each node;
 - `timing` --- print timing data for each node, if collecting of timing statistics is turned off on called side resulting output will contain WARNING message `timing statistics disabled`;
 - `buffers` --- print buffers usage, if collecting of buffers statistics is turned off on called side resulting output will contain WARNING message `buffers statistics disabled`;
 - `triggers` --- include triggers statistics in result plan trees;
 - `format` --- EXPLAIN format to be used for plans printing, posible values: {`text`, `xml`, `json`, `yaml`}.

If callable backend is not executing any query the function prints INFO message about backend's state taken from `pg_stat_activity` view if it exists there.

Calling role have to be superuser or member of the role whose backend is being called. Othrewise function prints ERROR message `permission denied`.

## Configuration settings
There are several user-accessible [GUC](https://www.postgresql.org/docs/9.5/static/config-setting.html) variables designed to toggle the whole module and the collecting of specific statistic parameters while query is running:

 - `pg_query_state.enable` --- disable (or enable) `pg_query_state` completely, default value is `true`
 - `pg_query_state.enable_timing` --- collect timing data for each node, default value is `false`
 - `pg_query_state.enable_buffers` --- collect buffers usage, default value is `false`

This parameters is set on called side before running any queries whose states are attempted to extract. **_Warning_**: if `pg_query_state.enable_timing` is turned off the calling side cannot get time statistics, similarly for `pg_query_state.enable_buffers` parameter.

## Examples
Set maximum number of parallel workers on `gather` node equals `2`:
```
postgres=# set max_parallel_workers_per_gather = 2;
```
Assume one backend with pid = 49265 performs a simple query:
```
postgres=# select pg_backend_pid();
 pg_backend_pid
 ----------------
          49265
(1 row)
postgres=# select count(*) from foo join bar on foo.c1=bar.c1;
```
Other backend can extract intermediate state of execution that query:
```
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
```
postgres=# select n_join_foo_bar();
```
Other backend can get the follow output:
```
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
```
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
Maksim Milyutin <m.milyutin@postgrespro.ru> Postgres Professional Ltd., Russia
