-- complain if script is sourced in psql, rather than via CREATE EXTENSION
\echo Use "ALTER EXTENSION pg_query_state UPDATE TO '1.1'" to load this file. \quit

DROP FUNCTION IF EXISTS executor_step(pid integer);
DROP FUNCTION IF EXISTS executor_continue(pid integer);
