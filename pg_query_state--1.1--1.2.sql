-- complain if script is sourced in psql, rather than via CREATE EXTENSION
\echo Use "ALTER EXTENSION pg_query_state UPDATE TO '1.2'" to load this file. \quit

CREATE FUNCTION pg_progress_bar(pid integer)
	RETURNS FLOAT
	AS 'MODULE_PATHNAME'
	LANGUAGE C STRICT VOLATILE;

CREATE FUNCTION pg_progress_bar_visual(pid		integer
								  , delay	integer = 1)
	RETURNS FLOAT
	AS 'MODULE_PATHNAME', 'pg_progress_bar'
	LANGUAGE C STRICT VOLATILE;
    