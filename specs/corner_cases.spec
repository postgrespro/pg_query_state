setup
{
	CREATE EXTENSION pg_query_state;
	CREATE ROLE alice;
	CREATE ROLE bob;
	CREATE ROLE super SUPERUSER;
	CREATE TABLE IF NOT EXISTS pid_storage (
			id integer CONSTRAINT unique_entry UNIQUE,
			pid integer
	);
	CREATE OR REPLACE FUNCTION save_own_pid(integer) RETURNS void AS $$
		BEGIN
			INSERT INTO pid_storage VALUES($1, pg_backend_pid())
				ON CONFLICT ON CONSTRAINT unique_entry DO
					UPDATE SET pid = pg_backend_pid();
		END;
	$$ LANGUAGE plpgsql;
	CREATE OR REPLACE FUNCTION counterpart_pid(integer) RETURNS integer AS $$
		BEGIN
			return (SELECT pid FROM pid_storage WHERE id = $1);
		END;
	$$ LANGUAGE plpgsql;
	GRANT SELECT, INSERT, UPDATE on pid_storage to alice, bob;
}

teardown
{
	DROP FUNCTION save_own_pid(integer);
	DROP FUNCTION counterpart_pid(integer);
	DROP TABLE pid_storage;
	DROP ROLE super;
	DROP ROLE bob;
	DROP ROLE alice;
	DROP EXTENSION pg_query_state;
}

session "s1"
step "s1_save_pid"			{ select save_own_pid(0); }
step "s1_pg_qs_counterpart" { select pg_query_state(counterpart_pid(1)); }
step "s1_set_bob"			{ set role bob; }
step "s1_disable_pg_qs"		{ set pg_query_state.enable to off; }
step "s1_enable_pg_qs"		{ set pg_query_state.enable to on; }
step "s1_pg_qs_1"			{ select pg_query_state(1); }
step "s1_pg_qs_2"			{ select pg_query_state(pg_backend_pid()); }
teardown
{
	reset role;
	set pg_query_state.enable to on;
}

session "s2"
step "s2_save_pid"			{ select save_own_pid(1); }
step "s2_pg_qs_counterpart" { select pg_query_state(counterpart_pid(0)); }
step "s2_set_bob"			{ set role bob; }
step "s2_set_alice"			{ set role alice; }
step "s2_set_su"			{ set role super; }
teardown
{
	reset role;
}

# Check invalid pid
permutation "s1_pg_qs_1"
permutation "s1_pg_qs_2"

# Check idle
permutation "s1_save_pid" "s2_pg_qs_counterpart"(*)

# Check module disable
permutation "s1_save_pid" "s1_disable_pg_qs" "s2_pg_qs_counterpart"(*)

# Check roles correspondence
permutation "s1_set_bob" "s2_set_bob" "s1_save_pid" "s2_pg_qs_counterpart"(*)
permutation "s1_set_bob" "s2_set_su" "s1_save_pid" "s2_pg_qs_counterpart"(*)
permutation "s1_set_bob" "s2_set_alice" "s1_save_pid" "s2_pg_qs_counterpart"(*)
