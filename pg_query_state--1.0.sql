-- complain if script is sourced in psql, rather than via CREATE EXTENSION
\echo Use "CREATE EXTENSION pg_query_state" to load this file. \quit

CREATE FUNCTION pg_query_state(pid 		integer
							 , verbose	boolean = FALSE
							 , costs 	boolean = FALSE
							 , timing 	boolean = FALSE
							 , buffers 	boolean = FALSE
							 , triggers	boolean = FALSE
						     , format	text = 'text')
	RETURNS TABLE (pid integer
				 , frame_number integer
				 , query_text text
				 , plan text
				 , leader_pid integer)
	AS 'MODULE_PATHNAME'
	LANGUAGE C STRICT VOLATILE;
