# pg\_query\_state
The `pg_query_state` module provides facility to know the current state of query execution on working backend. To enable this extension you have to patch the latest stable version of PostgreSQL. Different branches are intended for different version numbers of PostgreSQL, e.g., branch _PG9_5_ corresponds to PostgreSQL 9.5.

## Overview
Each complex query statement (SELECT/INSERT/UPDATE/DELETE) after optimization/planning stage is translated into plan tree wich is kind of imperative representation of declarative SQL query. EXPLAIN ANALYZE request allows to demonstrate execution statistics gathered from each node of plan tree (full time of execution, number rows emitted to upper nodes, etc). But this statistics is collected after execution of query. This module allows to show actual statistics of query running on external backend. At that, format of resulting output is almost identical to ordinal EXPLAIN ANALYZE. Thus users are able to track of query execution in progress.

In fact, this module is able to explore external backend and determine its actual state. Particularly it's helpful when backend executes a heavy query or gets stuck.

## Use cases
Using this module there can help in the following things:
 - detect a long query (along with other monitoring tools)
 - overwatch the query execution ([example](https://asciinema.org/a/c0jon1i6g92hnb5q4492n1rzn))

## Installation
To install `pg_query_state`, please apply patches `custom_signal.patch`, `executor_hooks.patch` and `runtime_explain.patch` to the latest stable version of PostgreSQL and rebuild PostgreSQL.

Correspondence branch names to PostgreSQL version numbers:
- _PG9_5_ --- PostgreSQL 9.5
- _PGPRO9_5_ --- PostgresPro 9.5
- _master_ --- development version for PostgreSQL 10devel

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
```
Extract current query state from backend with specified `pid`. Since parallel query can spawn workers and function call causes nested subqueries so that state of execution may be viewed as stack of running queries, return value of `pg_query_state` has type `TABLE (pid integer, frame_number integer, query_text text, plan text, leader_pid integer)`. It represents tree structure consisting of leader process and its spawned workers. Each worker refers to leader through `leader_pid` column. For leader process the value of this column is` null`. For each process the stack frames are specified as correspondence between `frame_number`, `query_text` and `plan` columns.

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
Assume one backend with pid = 20102 performs a simple query:
```
postgres=# select pg_backend_pid();
 pg_backend_pid
 ----------------
          20102
(1 row)
postgres=# select count(*) from foo join bar on foo.c1=bar.c1;
```
Other backend can extract intermediate state of execution that query:
```
postgres=# \x
postgres=# select * from pg_query_state(20102);
-[ RECORD 1 ]-----------------------------------------------------------------------------------
query_text | select count(*) from foo join bar on foo.c1=bar.c1;
plan       | Aggregate (Current loop: actual rows=0, loop number=1)                                                 +
           |   ->  Nested Loop (Current loop: actual rows=6, loop number=1)                                         +
           |         Join Filter: (foo.c1 = bar.c1)                                                                 +
           |         Rows Removed by Join Filter: 0                                                                 +
           |         ->  Seq Scan on bar (Current loop: actual rows=6, loop number=1)                               +
           |         ->  Materialize (actual rows=1000001 loops=5) (Current loop: actual rows=742878, loop number=6)+
           |               ->  Seq Scan on foo (Current loop: actual rows=1000001, loop number=1)
```
In example above `Materialize` node has statistics on passed loops (average number of rows delivered to `Nested Loop` and number of passed loops are shown) and statistics on current loop. Other nodes has statistics only for current loop as this loop is first (`loop number` = 1).

Assume first backend executes some function:
```
postgres=# select n_join_foo_bar();
```
Other backend can get the follow output:
```
postgres=# select * from pg_query_state(20102);
-[ RECORD 1 ]---------------------------------------------------------------------------------------------------------------
query_text | select n_join_foo_bar();
plan       | Result (Current loop: actual rows=0, loop number=1)
-[ RECORD 2 ]---------------------------------------------------------------------------------------------------------------
query_text | SELECT (select count(*) from foo join bar on foo.c1=bar.c1)
plan       | Result (Current loop: actual rows=0, loop number=1)                                                            +
           |   InitPlan 1 (returns $0)                                                                                      +
           |     ->  Aggregate (Current loop: actual rows=0, loop number=1)                                                 +
           |           ->  Nested Loop (Current loop: actual rows=8, loop number=1)                                         +
           |                 Join Filter: (foo.c1 = bar.c1)                                                                 +
           |                 Rows Removed by Join Filter: 0                                                                 +
           |                 ->  Seq Scan on bar (Current loop: actual rows=9, loop number=1)                               +
           |                 ->  Materialize (actual rows=1000001 loops=8) (Current loop: actual rows=665090, loop number=9)+
           |                       ->  Seq Scan on foo (Current loop: actual rows=1000001, loop number=1)
```
First row corresponds to function call, second - to query which is in the body of that function.

We can get result plans in different format (e.g. `json`):
```
postgres=# select * from pg_query_state(pid := 20102, format := 'json');
-[ RECORD 1 ]-----------------------------------------------------------
query_text | select n_join_foo_bar();
plan       | {                                                          +
           |   "Plan": {                                                +
           |     "Node Type": "Result",                                 +
           |     "Current loop": {                                      +
           |       "Actual Loop Number": 1,                             +
           |       "Actual Rows": 0                                     +
           |     }                                                      +
           |   }                                                        +
           | }
-[ RECORD 2 ]-----------------------------------------------------------
query_text | SELECT (select count(*) from foo join bar on foo.c1=bar.c1)
plan       | {                                                          +
           |   "Plan": {                                                +
           |     "Node Type": "Result",                                 +
           |     "Current loop": {                                      +
           |       "Actual Loop Number": 1,                             +
           |       "Actual Rows": 0                                     +
           |     },                                                     +
           |     "Plans": [                                             +
           |       {                                                    +
           |         "Node Type": "Aggregate",                          +
           |         "Strategy": "Plain",                               +
           |         "Parent Relationship": "InitPlan",                 +
           |         "Subplan Name": "InitPlan 1 (returns $0)",         +
           |         "Current loop": {                                  +
           |           "Actual Loop Number": 1,                         +
           |           "Actual Rows": 0                                 +
           |         },                                                 +
           |         "Plans": [                                         +
           |           {                                                +
           |             "Node Type": "Hash Join",                      +
           |             "Parent Relationship": "Outer",                +
           |             "Join Type": "Inner",                          +
           |             "Current loop": {                              +
           |               "Actual Loop Number": 1,                     +
           |               "Actual Rows": 124911                        +
           |             },                                             +
           |             "Hash Cond": "(foo.c1 = bar.c1)",              +
           |             "Plans": [                                     +
           |               {                                            +
           |                 "Node Type": "Seq Scan",                   +
           |                 "Parent Relationship": "Outer",            +
           |                 "Relation Name": "foo",                    +
           |                 "Alias": "foo",                            +
           |                 "Current loop": {                          +
           |                   "Actual Loop Number": 1,                 +
           |                   "Actual Rows": 1000004                   +
           |                 }                                          +
           |               },                                           +
           |               {                                            +
           |                 "Node Type": "Hash",                       +
           |                 "Parent Relationship": "Inner",            +
           |                 "Current loop": {                          +
           |                   "Actual Loop Number": 1,                 +
           |                   "Actual Rows": 500000                    +
           |                 },                                         +
           |                 "Hash Buckets": 131072,                    +
           |                 "Original Hash Buckets": 131072,           +
           |                 "Hash Batches": 8,                         +
           |                 "Original Hash Batches": 8,                +
           |                 "Peak Memory Usage": 3221,                 +
           |                 "Plans": [                                 +
           |                   {                                        +
           |                     "Node Type": "Seq Scan",               +
           |                     "Parent Relationship": "Outer",        +
           |                     "Relation Name": "bar",                +
           |                     "Alias": "bar",                        +
           |                     "Current loop": {                      +
           |                       "Actual Loop Number": 1,             +
           |                       "Actual Rows": 500000                +
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
```

## Functions for tracing query execution
For the purpose to achieve a slightly deterministic result from `pg_query_state` function under regression tests this module introduces specific functions for query tracing running on external backend process. In this case query is suspended after any node has worked off one step in pipeline structure of plan tree execution. Thus we can execute query specific number of steps and get its state which will be deterministic at least on number of emitted rows of each node.

Function `executor_step` which takes `pid` of traceable backend provides facility to perform single step of query execution. Function `executor_continue` which also takes `pid` completes query without trace interrupts.

Trace mode is set through GUC parameter `pg_query_state.executor_trace` which default is `off`. **_Warning_**: after setting this parameter any following queries (even specified implicitly, e.g., autocompletion of input in _psql_) will be interrupted and to resume their `executor_continue` must be accomplished on external backend. Only after that user can turn off trace mode.

### Examples with trace mode
Assume one backend with pid = 20102 sets trace mode and executes a simple query:
```
postgres=# set pg_query_state.executor_trace to on;
SET
postgres=# select count(*) from foo join bar on foo.c1=bar.c1;
```
This query is suspended. Then other backend can extract its state:
```
postgres=# select * from pg_query_state(pid := 20102);
-[ RECORD 1 ]------------------------------------------------------------------------------
query_text | select count(*) from foo join bar on foo.c1=bar.c1;
plan       | Aggregate (Current loop: actual rows=0, loop number=1)                        +
           |   ->  Hash Join (Current loop: actual rows=0, loop number=1)                  +
           |         Hash Cond: (foo.c1 = bar.c1)                                          +
           |         ->  Seq Scan on foo (Current loop: actual rows=0, loop number=1)      +
           |         ->  Hash (Current loop: actual rows=0, loop number=1)                 +
           |               ->  Seq Scan on bar (Current loop: actual rows=0, loop number=1)
```
As you can see none of nodes is executed. We can make one step of execution and see renewed state of query:
```
postgres=# select executor_step(20102);
-[ RECORD 1 ]-+-
executor_step | 

postgres=# select * from pg_query_state(pid := 20102);
-[ RECORD 1 ]------------------------------------------------------------------------------
query_text | select count(*) from foo join bar on foo.c1=bar.c1;
plan       | Aggregate (Current loop: actual rows=0, loop number=1)                        +
           |   ->  Hash Join (Current loop: actual rows=0, loop number=1)                  +
           |         Hash Cond: (foo.c1 = bar.c1)                                          +
           |         ->  Seq Scan on foo (Current loop: actual rows=1, loop number=1)      +
           |         ->  Hash (Current loop: actual rows=0, loop number=1)                 +
           |               ->  Seq Scan on bar (Current loop: actual rows=0, loop number=1)
```
Node `Seq Scan on foo` has emitted first row to `Hash Join`. Completion of traceable query is performed as follows:
```
postgres=# select executor_continue(pid := 20102);
-[ RECORD 1 ]-----+-
executor_continue | 
```
At the same time first backend prints result of query execution:
```
postgres=# select count(*) from foo join bar on foo.c1=bar.c1;
-[ RECORD 1 ]-
count | 500000
```

## Feedback
Do not hesitate to post your issues, questions and new ideas at the [issues](https://github.com/postgrespro/pg_query_state/issues) page.

## Authors
Maksim Milyutin <m.milyutin@postgrespro.ru> Postgres Professional Ltd., Russia
