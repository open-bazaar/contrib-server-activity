BEGIN TRANSACTION;
CREATE TABLE account_roles (
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
            PRIMARY KEY(account_id, role_id)
        );
INSERT INTO "account_roles" VALUES('account','reader');
CREATE TABLE accounts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            language TEXT,
            theme TEXT
        , auth_type TEXT NOT NULL DEFAULT 'local', session_version INTEGER NOT NULL DEFAULT 0);
INSERT INTO "accounts" VALUES('account','Owner','test-only',1,NULL,NULL,'local',0);
CREATE TABLE auth_settings (
            id INTEGER PRIMARY KEY CHECK(id = 1),
            default_role_id TEXT REFERENCES roles(id)
        , login_order TEXT NOT NULL DEFAULT '["password"]');
INSERT INTO "auth_settings" VALUES(1,NULL,'["password","work"]');
CREATE TABLE computers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            secret_hash TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL
        );
INSERT INTO "computers" VALUES('computer','Workshop','test-only',1);
CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computer_id TEXT NOT NULL REFERENCES computers (id),
            run_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            agent_id TEXT,
            event_name TEXT NOT NULL,
            thread_id TEXT,
            created_at_ms INTEGER NOT NULL,
            received_at_ms INTEGER NOT NULL,
            payload TEXT NOT NULL,
            UNIQUE (computer_id, run_id, seq)
        );
CREATE TABLE oidc_identities (
            issuer TEXT NOT NULL, subject TEXT NOT NULL,
            account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            provider_id TEXT NOT NULL REFERENCES oidc_providers(id),
            display_name TEXT NOT NULL, email TEXT NOT NULL,
            PRIMARY KEY(issuer, subject)
        );
CREATE TABLE oidc_providers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, logo_url TEXT NOT NULL,
            issuer TEXT NOT NULL, client_id TEXT NOT NULL, client_secret TEXT NOT NULL,
            redirect_uri TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        , description TEXT NOT NULL DEFAULT '', default_role_id TEXT REFERENCES roles(id), session_minutes INTEGER NOT NULL DEFAULT 10);
INSERT INTO "oidc_providers" VALUES('work','Work','','https://identity.example','test-client','test-only','https://app.example/callback',2,2,'',NULL,10);
CREATE TABLE refs (
            id INTEGER PRIMARY KEY,
            value TEXT NOT NULL UNIQUE
        );
CREATE TABLE relations (
            subject_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            ext TEXT,
            created_at_ms INTEGER NOT NULL,
            PRIMARY KEY (subject_id, kind, target_id)
        );
CREATE TABLE roles (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            permissions TEXT NOT NULL, created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL
        );
INSERT INTO "roles" VALUES('reader','Reader','["read"]',1,1);
CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY,
            migration_name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at_ms INTEGER NOT NULL,
            duration_ms INTEGER NOT NULL
        );
INSERT INTO "schema_migrations" VALUES(1,'initial_server_schema','b28af1899bcb79c5739dfb4e07648b6a711cb7f8f55bc9259be32e08bf8fc67b',1792214656104,1);
INSERT INTO "schema_migrations" VALUES(2,'refs','45df3659796d1424456f0df86f0f8b20d8c6999f1399be790104ea1adc8782dd',1792214656107,0);
INSERT INTO "schema_migrations" VALUES(3,'roles_oidc','ab45430386d48eb19663d2504d753f620683f869c6991b5b519681652da0fa4e',1792214656111,1);
INSERT INTO "schema_migrations" VALUES(4,'login_order','a4d50bbde8f0992dbe691f9cfbb847740adc56ea25b8ed173a1a12c71f2b06d8',1792214656131,0);
INSERT INTO "schema_migrations" VALUES(5,'provider_description','b5a7ce4886a7fc1462355ffadae6d5c5e23ce8be32040ba6cbcc8ffd96e474bd',1792214656134,0);
INSERT INTO "schema_migrations" VALUES(6,'provider_default_role','5904387a9c426c88a1cf14f15ffc960472eb5e7ff0f298466971acd2a311f131',1792214656137,0);
INSERT INTO "schema_migrations" VALUES(7,'oidc_session_settings','4c5dd26ea8e99b73d2a977b570e9a515cd685d66b74c8ebd7b68576cf349ccf6',1792214656140,0);
CREATE INDEX events_by_agent ON events (computer_id, agent_id, id);
CREATE INDEX events_by_name ON events (computer_id, event_name, agent_id, id);
CREATE INDEX events_by_thread ON events (computer_id, agent_id, thread_id, created_at_ms);
CREATE INDEX relations_by_target ON relations (kind, target_id);
CREATE INDEX account_roles_by_role ON account_roles(role_id);
CREATE INDEX oidc_identities_by_account ON oidc_identities(account_id);
INSERT INTO "events" VALUES(1,'computer','run-0.2',1,'agent-1','channel.inbound.persisted','thread-1',1792214700000,1792214700040,'{"event_name":"channel.inbound.persisted","state":"completed","created_at_ms":1792214700000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","inbound_seq":41},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"notifies_runtime":true,"review":"approved","channel_session_mapping":"reused","thread_mapping":"reused","text":"帮我看看 CI","target":"-100","target_kind":"group","target_name":"#dev","message_type":"text","sender":{"id":"7","name":"yuchanns","display_name":"yuchanns","kind":"human"},"reply_to_message_id":null,"attachments":[]}}');
INSERT INTO "events" VALUES(2,'computer','run-0.2',2,'agent-1','runtime.request.turn.started','thread-1',1792214701000,1792214701040,'{"event_name":"runtime.request.turn.started","state":"completed","created_at_ms":1792214701000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","runtime_session_id":"rs-1","turn_id":"turn-1","inbound_seq":41,"provider_turn_id":null},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"provider_method":"turn/start"}}');
INSERT INTO "events" VALUES(3,'computer','run-0.2',3,'agent-1','tool_call.started','thread-1',1792214702000,1792214702040,'{"event_name":"tool_call.started","state":"completed","created_at_ms":1792214702000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","runtime_session_id":"rs-1","turn_id":"turn-1","inbound_seq":41,"provider_turn_id":null},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"call_id":"call-1","name":"shell","parent_call_id":null}}');
INSERT INTO "events" VALUES(4,'computer','run-0.2',4,'agent-1','tool_call.completed','thread-1',1792214704000,1792214704040,'{"event_name":"tool_call.completed","state":"completed","created_at_ms":1792214704000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","runtime_session_id":"rs-1","turn_id":"turn-1","inbound_seq":41,"provider_turn_id":null},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"call_id":"call-1","name":"shell","parent_call_id":null}}');
INSERT INTO "events" VALUES(5,'computer','run-0.2',5,'agent-1','usage.updated','thread-1',1792214705000,1792214705040,'{"event_name":"usage.updated","state":"completed","created_at_ms":1792214705000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","runtime_session_id":"rs-1","turn_id":"turn-1","inbound_seq":41,"provider_turn_id":null},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"total":{"input_tokens":1200,"cached_input_tokens":3000,"cache_write_input_tokens":100,"output_tokens":300,"total_tokens":4600},"last":null,"cost_usd":0.25}}');
INSERT INTO "events" VALUES(6,'computer','run-0.2',6,'agent-1','usage.updated','thread-1',1792214709000,1792214709040,'{"event_name":"usage.updated","state":"completed","created_at_ms":1792214709000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","runtime_session_id":"rs-1","turn_id":"turn-1","inbound_seq":41,"provider_turn_id":null},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"total":{"input_tokens":2000,"cached_input_tokens":5000,"cache_write_input_tokens":100,"output_tokens":500,"total_tokens":7600},"last":null,"cost_usd":0.5}}');
INSERT INTO "events" VALUES(7,'computer','run-0.2',7,'agent-1','channel.outbound.sent','thread-1',1792214710000,1792214710040,'{"event_name":"channel.outbound.sent","state":"completed","created_at_ms":1792214710000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","inbound_seq":41,"outbound_message_id":"out-1"},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"text":"CI 全部通过了。","attachments":[]}}');
INSERT INTO "events" VALUES(8,'computer','run-0.2',8,'agent-1','runtime.turn.completed','thread-1',1792214711000,1792214711040,'{"event_name":"runtime.turn.completed","state":"completed","created_at_ms":1792214711000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","runtime_session_id":"rs-1","turn_id":"turn-1","inbound_seq":41,"provider_turn_id":null},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"provider_event":"turn/completed"}}');
INSERT INTO "events" VALUES(9,'computer','run-0.2',9,'agent-1','channel.inbound.persisted','thread-1',1792214760000,1792214760040,'{"event_name":"channel.inbound.persisted","state":"completed","created_at_ms":1792214760000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","inbound_seq":42},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"notifies_runtime":true,"review":"approved","channel_session_mapping":"reused","thread_mapping":"reused","text":"再看看 README","target":"-100","target_kind":"group","target_name":"#dev","message_type":"text","sender":{"id":"7","name":"yuchanns","display_name":"yuchanns","kind":"human"},"reply_to_message_id":null,"attachments":[]}}');
INSERT INTO "events" VALUES(10,'computer','run-0.2',10,'agent-1','runtime.request.turn.started','thread-1',1792214761000,1792214761040,'{"event_name":"runtime.request.turn.started","state":"completed","created_at_ms":1792214761000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","runtime_session_id":"rs-1","turn_id":"turn-2","inbound_seq":42,"provider_turn_id":null},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"provider_method":"turn/start"}}');
INSERT INTO "events" VALUES(11,'computer','run-0.2',11,'agent-1','runtime.turn.failed','thread-1',1792214790000,1792214790040,'{"event_name":"runtime.turn.failed","state":"completed","created_at_ms":1792214790000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-1","thread_id":"thread-1","provider_thread_id":"-100","runtime_session_id":"rs-1","turn_id":"turn-2","inbound_seq":42,"provider_turn_id":null},"level":"info","duration_ms":null,"error_kind":"provider_failed","error_type":null,"error_message":"runtime turn failed: TimeoutError","traceback_ref":null,"metadata":{"provider_event":"turn/failed"}}');
INSERT INTO "events" VALUES(12,'computer','run-0.2',12,NULL,'node.health',NULL,1792214820000,1792214820040,'{"event_name":"node.health","state":"completed","created_at_ms":1792214820000,"correlation":{},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"agents":[],"version":"0.2.3","system":"Linux","audit":{"queued":0}}}');
INSERT INTO "events" VALUES(13,'computer','run-0.2',13,'agent-1','channel.inbound.persisted','thread-2',1792214830000,1792214830040,'{"event_name":"channel.inbound.persisted","state":"completed","created_at_ms":1792214830000,"correlation":{"node_id":"agent-1","channel":"telegram","channel_session_id":"cs-2","thread_id":"thread-2","provider_thread_id":"42","inbound_seq":43},"level":"info","duration_ms":null,"error_kind":null,"error_type":null,"error_message":null,"traceback_ref":null,"metadata":{"notifies_runtime":false,"review":"pending","channel_session_mapping":"created","thread_mapping":"created","text":"你好，想请你帮我搭一下开发环境","target":"lilei","target_kind":"dm","target_name":null,"message_type":"text","sender":{"id":"42","name":"lilei","display_name":"李雷","kind":"human"},"reply_to_message_id":null,"attachments":[]}}');
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('events',13);
COMMIT;
