# Graph Report - Life-Inbox  (2026-11-13)

## Corpus Check
- 139 files · ~114,518 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 14 file(s) not represented in the graph (top: (none) 7, .ini 2, .css 2)

## Summary
- 2354 nodes · 5336 edges · 119 communities (97 shown, 22 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 165 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `442960ee`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- main.py
- database.py
- system.py
- typing
- test_api.py
- dates.py
- test_google_calendar.py
- intelligence.py
- app.js
- require_owner_id
- playful.js
- routers/calendar.py
- test_auth_isolation.py
- test_assistant.py
- routers/auth.py
- pipeline.py
- test_profile_photos.py
- AppConfig
- me.py
- test_item_kinds.py
- test_flashcards_resurface.py
- environment
- assistant_routes.py
- _add_note
- privacy.py
- test_profile.py
- api_access.py
- test_expenses.py
- test_entities.py
- Architecture
- json
- fetchJson
- re
- tasks.py
- test_privacy.py
- setStatus
- caddy
- test_retrieval.py
- test_settings.py
- os
- ui.js
- app/auth.py
- ask.py
- google_calendar.py
- search.py
- image_extract.py
- importlib
- embeddings.py
- proxy
- sys
- test_public_api.py
- LocalFilesystemStorage
- test_semantic_search.py
- task_questions.py
- renderCalendar
- make_png_bytes
- test_gnani_stt.py
- _sync
- server.py
- _seed_tasks
- loadTopics
- cat_voice.js
- Architecture Visualization Tool Selection & Evaluation Report
- Manual testing / browser smoke checklist
- settings.js
- pytest
- auto_analysis.py
- filterAndRenderCards
- profile.js
- Life Inbox
- Deployment guide (private beta)
- make_wav_bytes
- test_ux_milestone3.py
- voice_extract.py
- Deploying Life Inbox on Oracle Cloud (Always Free)
- test_notifications.py
- disconnect
- _versioned_static_refs
- NeedsReconnect
- similarity.py
- make_text_pdf
- claim_local_data.py
- TokenSource
- create
- Product roadmap
- 3. Configure and migrate the database
- _user_to_dict
- calendar_sync_wanted
- refreshAfterAnalysis
- pagination_state.js
- token_crypto.py
- 0004_persist_analysis_results.py
- env.py
- Agent skills
- _usable_wav_as_is
- Domain Docs
- Issue tracker: Local Markdown
- test_attention_board_places_tasks_in_overdue_today_and_upcoming
- analyze_inbox_with_openai
- applyTimelineRange
- eval_search.py
- auto_client
- app/__init__.py
- RevalidatingStaticFiles
- promptAddPin
- _enable_sqlite_foreign_keys
- reset_calendar_adapter_for_tests
- theme.js
- client
- triage-labels.md
- milestone-status.md
- test_analyze_returns_friendly_error
- test_failed_analysis_keeps_previous_success
- test_list_notes_returns_friendly_error
- test_update_note_returns_friendly_error

## God Nodes (most connected - your core abstractions)
1. `require_owner_id()` - 66 edges
2. `fetchJson()` - 43 edges
3. `load_config()` - 31 edges
4. `AppConfig` - 30 edges
5. `sign_in()` - 28 edges
6. `parse_timing_phrase()` - 26 edges
7. `setStatus()` - 24 edges
8. `_add_note()` - 24 edges
9. `connect()` - 23 edges
10. `today_in_configured_tz()` - 22 edges

## Surprising Connections (you probably didn't know these)
- `Rich capture lifecycle` --references--> `list_notes_from_db()`  [INFERRED]
  docs/architecture.md → app/database.py
- `Voice assistant` --references--> `classify_utterance()`  [INFERRED]
  docs/voice-assistant.md → app/assistant.py
- `Attention & safe actions` --references--> `CalendarSyncer`  [INFERRED]
  docs/architecture.md → app/integrations/google_calendar.py
- `Privacy layer` --references--> `ask_ai()`  [INFERRED]
  README.md → app/privacy.py
- `Data model (current)` --references--> `UserSettings`  [INFERRED]
  docs/architecture.md → app/schemas.py

## Import Cycles
- None detected.

## Communities (119 total, 22 thin omitted)

### Community 0 - "main.py"
Cohesion: 0.06
Nodes (49): auto_analysis_enabled(), validate_image_upload(), extract_pdf_text(), validate_pdf_upload(), process_image_item(), delete_stored_file(), normalize_filename(), read_stored_bytes() (+41 more)

### Community 1 - "database.py"
Cohesion: 0.04
Nodes (55): add_api_token(), add_file_item_to_db(), add_image_item_to_db(), add_voice_item_to_db(), add_webhook(), AiCallLogRecord, AnalysisRunRecord, ApiTokenRecord (+47 more)

### Community 2 - "system.py"
Cohesion: 0.05
Nodes (56): delete_api_token(), delete_webhook(), dismiss_relation_in_db(), set_item_sensitive(), update_item_insight_overrides(), validate_analysis_result(), analyze_inbox(), ask_my_inbox() (+48 more)

### Community 3 - "typing"
Cohesion: 0.06
Nodes (23): upgrade(), _validate_existing_notes_table(), upgrade(), _copy_legacy_notes(), _create_or_validate_inbox_items(), upgrade(), upgrade(), upgrade() (+15 more)

### Community 4 - "test_api.py"
Cohesion: 0.03
Nodes (18): analysis_without_metadata(), test_analyze_empty_inbox(), test_analyze_uses_deterministic_fallback_without_openai_key(), test_canonical_item_rejects_invalid_text(), test_canonical_item_update_rejects_invalid_text(), test_create_note_returns_friendly_error(), test_delete_note_returns_friendly_error(), test_failed_url_capture_can_retry() (+10 more)

### Community 5 - "dates.py"
Cohesion: 0.07
Nodes (36): _after_span(), classify_attention_bucket(), configured_timezone(), find_timing_phrase(), format_clock(), format_deadline(), format_timestamp(), now_in_configured_tz() (+28 more)

### Community 6 - "test_google_calendar.py"
Cohesion: 0.10
Nodes (38): add_task(), connect(), ctx(), FakeGoogle, FakeResponse, finish(), sign_in(), start_connect() (+30 more)

### Community 7 - "intelligence.py"
Cohesion: 0.06
Nodes (29): add_flashcards(), _flashcard_to_dict(), get_entity_page(), get_flashcard(), list_due_flashcards(), list_entities(), list_items_by_kind(), list_items_from_db() (+21 more)

### Community 8 - "app.js"
Cohesion: 0.05
Nodes (43): allCardsCache, allEntitiesCache, askExample(), authOptions, clockLabel(), closeRecipeCookMode(), dashboardState, dashState (+35 more)

### Community 9 - "require_owner_id"
Cohesion: 0.07
Nodes (31): complete_analysis_run(), compute_inbox_fingerprint(), create_topic_in_db(), ensure_default_topics(), fail_analysis_run(), get_latest_successful_analysis(), get_organization_dashboard(), init_db() (+23 more)

### Community 10 - "playful.js"
Cohesion: 0.10
Nodes (35): addHappy(), bell(), build(), catSound(), chime(), cleanName(), closeMenu(), feed() (+27 more)

### Community 11 - "routers/calendar.py"
Cohesion: 0.10
Nodes (20): get_task_by_id(), save_calendar_receipt(), task_is_owned(), calendar_config(), CalendarAdapter, CalendarAdapterStatus, CalendarEventDraft, CalendarWriteBlockedError (+12 more)

### Community 12 - "test_auth_isolation.py"
Cohesion: 0.08
Nodes (25): ctx(), _fake_google(), FakeResponse, make_user(), _start(), test_automatic_analysis_stays_inside_each_users_inbox(), test_concurrent_users_stay_isolated(), test_cross_origin_writes_are_blocked() (+17 more)

### Community 13 - "test_assistant.py"
Cohesion: 0.08
Nodes (23): classify_utterance(), _float_env(), _reminder_request(), _starts_with_word(), _strip_filler(), synthesize_speech(), to_speech(), api() (+15 more)

### Community 14 - "routers/auth.py"
Cohesion: 0.10
Nodes (19): dev_login_user(), ConfigError, _csv(), load_config(), _truthy(), get_user_by_id(), revoke_all_auth_sessions(), auth_dev_login() (+11 more)

### Community 15 - "pipeline.py"
Cohesion: 0.09
Nodes (16): _db(), process_item(), process_pdf_item(), process_url_item(), process_voice_item(), _resolve(), fetch_url(), validate_public_http_url() (+8 more)

### Community 16 - "test_profile_photos.py"
Cohesion: 0.10
Nodes (19): extract_article(), _host_is_blocked(), _HTMLMetaParser, ctx(), FakeResponse, google_sign_in(), image_bytes(), make_user() (+11 more)

### Community 17 - "AppConfig"
Cohesion: 0.10
Nodes (15): AppConfig, _b64(), build_authorization(), clean_picture_url(), email_allowed(), fetch_google_identity(), GoogleAuthError, read_state_cookie() (+7 more)

### Community 18 - "me.py"
Cohesion: 0.07
Nodes (14): AnalysisAlreadyRunningError, AuthRequiredError, get_user_avatar_path(), set_user_avatar_path(), update_user_profile(), update_user_settings(), clean_date_of_birth(), _calendar_detail() (+6 more)

### Community 19 - "test_item_kinds.py"
Cohesion: 0.08
Nodes (16): build_prompt(), clean_card(), fallback_card(), guess_kind(), make_card(), to_number(), test_fallback_receipt_card(), client() (+8 more)

### Community 20 - "test_flashcards_resurface.py"
Cohesion: 0.09
Nodes (19): clean_cards(), fallback_cards(), next_review(), forgotten(), on_this_day(), resurface(), saved_on(), client() (+11 more)

### Community 21 - "environment"
Cohesion: 0.06
Nodes (31): environment, image, ports, volumes, DATABASE_URL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, LIFE_INBOX_ALLOW_DEV_LOGIN (+23 more)

### Community 22 - "assistant_routes.py"
Cohesion: 0.10
Nodes (16): content_words(), has_devanagari(), is_on_topic(), assistant_page(), assistant_speak(), assistant_voice(), _heard(), _reply() (+8 more)

### Community 23 - "_add_note"
Cohesion: 0.14
Nodes (26): _add_note(), _pending_ids(), _record_analyzer_calls(), _settle(), test_analyze_keeps_earlier_todos_and_adds_new_ones(), test_analyze_only_sends_new_notes_and_keeps_earlier_results(), test_analyze_with_nothing_new_does_not_call_the_organizer(), test_automatic_analysis_can_be_switched_off() (+18 more)

### Community 24 - "privacy.py"
Cohesion: 0.13
Nodes (15): add_ai_call_log(), build_prompt(), detect_entity_themes(), extract_clean_highlight(), extract_entities(), fallback_entity_summary(), format_human_range(), summarize_entity() (+7 more)

### Community 25 - "test_profile.py"
Cohesion: 0.16
Nodes (20): ctx(), edit(), FakeResponse, google_sign_in(), make_user(), test_bad_dates_are_refused(), test_bad_names_are_refused(), test_date_of_birth_can_be_saved_and_removed() (+12 more)

### Community 26 - "api_access.py"
Cohesion: 0.09
Nodes (11): deliver_webhooks(), hash_token(), new_token(), sign(), token_from_header(), bind_owner_from_session(), _may_change_tasks(), _owner_for_api_token() (+3 more)

### Community 27 - "test_expenses.py"
Cohesion: 0.12
Nodes (14): expense_summary(), find_amount(), find_date(), receipt_month(), client(), fake_ai(), receipt(), test_ai_amount_as_text_becomes_a_number() (+6 more)

### Community 28 - "test_entities.py"
Cohesion: 0.13
Nodes (16): clean_entities(), clean_name(), fallback_entities(), add(), client(), fake_ai(), test_clean_entities_filters_bad_rows_and_duplicates(), test_clean_entities_rejects_wrong_shape() (+8 more)

### Community 29 - "Architecture"
Cohesion: 0.09
Nodes (12): CalendarSyncer, UserSettings, Analysis freshness, Architecture, Attention & safe actions, Core modules, Data model (current), External services (+4 more)

### Community 30 - "json"
Cohesion: 0.15
Nodes (13): fingerprint(), _source_key(), upgrade(), find(), union(), run_helper(), test_delete_loaded_note_decrements_offset_and_removes_item(), test_delete_then_load_more_includes_note_101() (+5 more)

### Community 31 - "fetchJson"
Cohesion: 0.13
Nodes (22): createApiToken(), createWebhook(), dashAddNote(), dashSearchAsk(), deleteApiToken(), deleteWebhook(), esc(), fetchJson() (+14 more)

### Community 32 - "re"
Cohesion: 0.14
Nodes (9): match_task(), parse_task_command(), snooze_date(), _words(), clean_display_name(), latest_birth_date(), test_done_and_snooze_are_parsed(), test_snooze_dates() (+1 more)

### Community 33 - "tasks.py"
Cohesion: 0.15
Nodes (17): confirm_task_timing(), get_daily_briefing(), get_user_settings(), set_task_reminder_in_db(), attention_board(), complete_task(), confirm_timing(), daily_briefing() (+9 more)

### Community 34 - "test_privacy.py"
Cohesion: 0.11
Nodes (10): redact(), restore(), client(), fake_ai(), test_ai_calls_are_logged_without_prompt_text(), test_analyze_never_sends_sensitive_text_or_phone_numbers(), test_ask_never_sends_sensitive_items(), test_redact_hides_personal_details() (+2 more)

### Community 35 - "setStatus"
Cohesion: 0.24
Nodes (23): actionButton(), addImage(), addNote(), addPdf(), addUrl(), addVoice(), askInbox(), createTopic() (+15 more)

### Community 36 - "caddy"
Cohesion: 0.09
Nodes (22): depends_on, environment, image, ports, restart, volumes, ports, restart (+14 more)

### Community 37 - "test_retrieval.py"
Cohesion: 0.16
Nodes (17): client(), _kw(), _mock_ask_openai(), _passage(), _seed_atomic_habits(), test_ask_falls_back_on_malformed_model_response(), test_ask_falls_back_on_openai_api_error(), test_ask_falls_back_on_unvalidated_citation_ids() (+9 more)

### Community 38 - "test_settings.py"
Cohesion: 0.19
Nodes (17): add_task(), apps(), make_user(), me(), save(), test_accounts_without_google_say_so(), test_ai_shows_as_on_when_the_server_has_a_key(), test_bad_settings_are_refused() (+9 more)

### Community 39 - "os"
Cohesion: 0.15
Nodes (10): _convert_to_wav_segments(), _error_message(), GnaniError, _language_code(), _model(), _transcribe_segment(), transcribe_with_gnani(), extract_json_array() (+2 more)

### Community 40 - "ui.js"
Cohesion: 0.13
Nodes (10): activate(), activePane(), moveIndicator(), playIntro(), revealPanel(), setMode(), showToast(), tabButton() (+2 more)

### Community 41 - "app/auth.py"
Cohesion: 0.19
Nodes (12): clear_session_cookie(), current_user_from_request(), end_session(), get_current_user(), optional_current_user(), origin_allowed(), set_session_cookie(), start_session() (+4 more)

### Community 42 - "ask.py"
Cohesion: 0.16
Nodes (15): get_sensitive_item_ids(), _build_citations(), build_grounded_answer(), _citation_id(), _extractive_answer(), _extractive_payload(), _insufficient_payload(), _pack_evidence() (+7 more)

### Community 43 - "google_calendar.py"
Cohesion: 0.18
Nodes (10): _db(), _error_reasons(), event_for_task(), _expiry(), finish_connect(), _http(), _json(), _now() (+2 more)

### Community 44 - "search.py"
Cohesion: 0.21
Nodes (10): load_passages(), _split_passages(), TextPassage, _tokenize(), _build_tfidf_vectors(), _cosine(), _idf(), _keyword_score() (+2 more)

### Community 45 - "image_extract.py"
Cohesion: 0.15
Nodes (8): describe_image(), _fallback_description(), make_thumbnail_bytes(), process_image_bytes(), run_ocr(), _sniff_image(), make_avatar_bytes(), make_jpeg_bytes()

### Community 46 - "importlib"
Cohesion: 0.13
Nodes (6): client(), test_cat_voice_script_is_loaded_after_playful(), client(), test_main_page_loads_the_shared_ribbon_before_ui(), ctx(), ctx()

### Community 47 - "embeddings.py"
Cohesion: 0.17
Nodes (10): EmbeddingCacheRecord, load_cached_vectors(), save_cached_vectors(), cosine(), embed_texts(), embeddings_enabled(), get_vectors(), meaning_scores() (+2 more)

### Community 48 - "proxy"
Cohesion: 0.12
Nodes (17): ports, restart, path, required, networks, default, proxy, aliases (+9 more)

### Community 50 - "test_public_api.py"
Cohesion: 0.12
Nodes (5): client(), main(), sent(), test_broken_receiver_does_not_break_saving(), test_webhook_fires_on_new_item_with_valid_signature()

### Community 51 - "LocalFilesystemStorage"
Cohesion: 0.15
Nodes (3): get_storage_backend(), LocalFilesystemStorage, StorageBackend

### Community 52 - "test_semantic_search.py"
Cohesion: 0.17
Nodes (10): client(), fake_embed(), search(), test_cosine(), test_edited_item_is_embedded_again(), test_finds_note_with_no_shared_words(), test_model_failure_falls_back_to_keywords(), test_passage_vectors_are_cached() (+2 more)

### Community 53 - "task_questions.py"
Cohesion: 0.23
Nodes (10): answer_task_question(), _clock(), _flags(), is_task_question(), _payload(), _plural(), _scope(), _tokens() (+2 more)

### Community 54 - "renderCalendar"
Cohesion: 0.14
Nodes (16): addAgendaItem(), jumpToToday(), navMonth(), renderCalendar(), renderCalendarDaysGrid(), renderDueTodayList(), renderOverdueList(), renderSelectedDayAgenda() (+8 more)

### Community 55 - "make_png_bytes"
Cohesion: 0.12
Nodes (11): make_png_bytes(), test_analyze_includes_ready_image_items(), test_failed_image_capture_can_retry(), flaky_process(), test_failed_voice_capture_can_retry(), test_image_capture_persists_before_processing_and_creates_preview(), test_image_ocr_text_is_editable(), test_pdf_capture_persists_before_processing_and_extracts_text() (+3 more)

### Community 56 - "test_gnani_stt.py"
Cohesion: 0.23
Nodes (11): _env(), _FakeResponse, _silent_wav(), test_auth_error_is_friendly_and_hides_key(), test_gnani_failure_without_openai_keeps_audio_with_note(), test_long_audio_is_split_and_joined(), fake_post(), test_rate_limit_retries_once() (+3 more)

### Community 57 - "_sync"
Cohesion: 0.32
Nodes (4): CalendarAPI, CalendarSyncError, _raise_for(), _sync()

### Community 58 - "server.py"
Cohesion: 0.22
Nodes (6): add_note(), ask_inbox(), call_api(), search_inbox(), what_needs_attention(), Public API, webhooks and MCP server

### Community 59 - "_seed_tasks"
Cohesion: 0.23
Nodes (13): _ask(), _freeze_clock(), _seed_mixed_tasks(), _seed_tasks(), test_ambiguous_dates_are_used_without_asking_for_confirmation(), test_ask_about_other_things_still_searches_notes(), test_ask_all_tasks_includes_undated_ones(), test_ask_filters_tasks_by_topic_and_falls_back_to_notes() (+5 more)

### Community 60 - "loadTopics"
Cohesion: 0.16
Nodes (14): applyItemFilters(), clearItemFilters(), hasItemFilters(), itemFilterParams(), loadTopics(), notesEmptyState(), refreshNoteDetails(), renderNoteMeta() (+6 more)

### Community 61 - "cat_voice.js"
Cohesion: 0.37
Nodes (13): ask(), browserVoice(), cancelListening(), cleanup(), downsample(), encodeWav(), finishListening(), onTap() (+5 more)

### Community 62 - "Architecture Visualization Tool Selection & Evaluation Report"
Cohesion: 0.14
Nodes (13): 1. Executive Summary & Objective, 2. Evaluation Dimensions, 3.1 Graphify (`graphify-labs/graphify@graphify`), 3.2 Archify (`tt-a1i/archify@archify`), 3.3 Floorplan (`haydenschmitty/floorplan@floorplan`), 3.4 Understand-Anything (`egonex-ai/understand-anything`), 3. Candidate In-Depth Analysis, 4. Comprehensive Evaluation Matrix (+5 more)

### Community 63 - "Manual testing / browser smoke checklist"
Cohesion: 0.14
Nodes (13): Ask My Inbox smoke (Milestone 4+), Attention / Calendar smoke (Milestone 5+), Core smoke (Milestone 1+), Do not, Error states, Image capture smoke (Milestone 3C+), Manual testing / browser smoke checklist, Multi-user / auth smoke (Milestone 6+) (+5 more)

### Community 64 - "settings.js"
Cohesion: 0.37
Nodes (12): calendarControls(), call(), disconnectCalendar(), makeButton(), makeNote(), refreshApps(), save(), setStatus() (+4 more)

### Community 65 - "pytest"
Cohesion: 0.27
Nodes (5): alembic_config(), test_topic_names_are_unique_per_owner_after_upgrade(), test_upgrade_adopts_legacy_database_without_changing_notes(), test_upgrade_consolidates_tasks_that_share_a_source(), test_upgrade_creates_inbox_items_in_empty_database()

### Community 67 - "filterAndRenderCards"
Cohesion: 0.20
Nodes (12): clearCardsFilters(), closeNewCardModal(), convertAllNotesToCards(), cycleBookStatus(), filterAndRenderCards(), filterCardsByCategory(), filterCardsByKind(), loadCards() (+4 more)

### Community 68 - "profile.js"
Cohesion: 0.35
Nodes (11): call(), changes(), formatDate(), initialsFor(), load(), photoRequest(), refreshButtons(), setPill() (+3 more)

### Community 69 - "Life Inbox"
Cohesion: 0.17
Nodes (9): 1. Requirements, 2. Set up the project, 4. Optional: configure OpenAI, 5. Start Life Inbox, 6. Run the automated tests, 7. Optional: add demonstration notes, Life Inbox, Project layout (+1 more)

### Community 70 - "Deployment guide (private beta)"
Cohesion: 0.17
Nodes (12): Container / Postgres, Deployment guide (private beta), Environment variables, Environments, Google Calendar, Local run (dev login), Local run (Google sign-in), Migrations / backups / rollback (+4 more)

### Community 71 - "make_wav_bytes"
Cohesion: 0.23
Nodes (10): _mock_openai_transcription(), create(), test_voice_transcript_flows_into_ask_retrieval(), test_voice_transcript_is_editable_and_analyzable(), test_voice_transcription_api_failure_preserves_audio(), test_voice_transcription_no_key_keeps_item_editable(), test_voice_transcription_owner_isolation(), test_voice_transcription_success_persists_transcript() (+2 more)

### Community 72 - "test_ux_milestone3.py"
Cohesion: 0.17
Nodes (5): test_rich_informative_empty_states(), test_toast_wired_for_mcp_and_ai_transformations(), test_universal_toast_engine_in_ui_and_css(), test_ux_layout_navigation_and_typography_polish(), test_zero_blocking_browser_dialogs_in_app_js()

### Community 73 - "voice_extract.py"
Cohesion: 0.25
Nodes (6): gnani_configured(), _friendly_transcription_unavailable_note(), process_voice_bytes(), _sniff_audio(), transcribe_audio(), _transcription_model()

### Community 74 - "Deploying Life Inbox on Oracle Cloud (Always Free)"
Cohesion: 0.18
Nodes (11): 1. Create the VM (Oracle console, ~10 minutes), 2. Open ports 80 and 443, 3. Install Docker, 4. Get the code and write `.env`, 5. Start it, 6. Create an API token (for scripts and the MCP server), 7. Connect Claude Desktop (MCP), Deploying Life Inbox on Oracle Cloud (Always Free) (+3 more)

### Community 75 - "test_notifications.py"
Cohesion: 0.35
Nodes (7): add_task(), bell(), make_user(), test_bell_sits_between_volume_and_profile(), test_finished_tasks_and_other_peoples_tasks_are_left_out(), test_nothing_pending(), test_tasks_are_ordered_high_to_low_then_by_urgency()

### Community 76 - "disconnect"
Cohesion: 0.20
Nodes (4): calendar_feature_enabled(), calendar_status(), disconnect(), google_calendar_disconnect()

### Community 77 - "_versioned_static_refs"
Cohesion: 0.24
Nodes (6): config_warnings(), health(), index(), profile_page(), settings_page(), _versioned_static_refs()

### Community 78 - "NeedsReconnect"
Cohesion: 0.20
Nodes (3): CalendarConnectError, CalendarGone, NeedsReconnect

### Community 79 - "similarity.py"
Cohesion: 0.31
Nodes (6): classify_relation(), find_item_relations(), jaccard(), normalize_text(), similarity_score(), token_set()

### Community 80 - "make_text_pdf"
Cohesion: 0.20
Nodes (6): make_blank_pdf(), make_text_pdf(), test_analyze_includes_ready_pdf_items(), test_blank_pdf_fails_with_helpful_message(), test_failed_pdf_capture_can_retry(), test_pdf_capture_is_analyzed_automatically()

### Community 81 - "claim_local_data.py"
Cohesion: 0.33
Nodes (4): count_owned_data(), get_user_by_email(), transfer_owned_data(), main()

### Community 82 - "TokenSource"
Cohesion: 0.25
Nodes (3): sync_tasks(), TokenSource, _sync_calendar_for_owner()

### Community 83 - "create"
Cohesion: 0.39
Nodes (8): create(), compile(), degrade(), draw(), init(), loop(), resize(), sync()

### Community 84 - "Product roadmap"
Cohesion: 0.22
Nodes (8): Execution priority (time-constrained), Milestone 1 — Polished, trustworthy core, Milestone 2 — Intelligent organization / second brain, Milestone 3 — Rich capture, Milestone 4 — Retrieval + grounded “Ask My Inbox” (done), Milestone 5 — Attention, reminders, safe actions (done), Milestone 6 — Deployable multi-user beta (done), Product roadmap

### Community 85 - "3. Configure and migrate the database"
Cohesion: 0.22
Nodes (9): 3. Configure and migrate the database, Canonical inbox-item model, People and entities, Persisted analysis, Persistent tasks, Receipts and expenses, Resurfacing and flashcards, Semantic search (meaning, not just keywords) (+1 more)

### Community 86 - "_user_to_dict"
Cohesion: 0.39
Nodes (6): _avatar_url(), ensure_user(), _settings_for(), upsert_google_user(), _user_to_dict(), UserRecord

### Community 87 - "calendar_sync_wanted"
Cohesion: 0.25
Nodes (5): calendar_sync_wanted(), set_current_owner_id(), _auto_analyze_owner(), _schedule_calendar_sync(), schedule_calendar_sync()

### Community 88 - "refreshAfterAnalysis"
Cohesion: 0.25
Nodes (8): captureTypeLabel(), fillNoteTags(), populateCategoryFilter(), refreshAfterAnalysis(), rememberNoteInsights(), sleep(), waitForAutoAnalysis(), watchAnalysis()

### Community 91 - "0004_persist_analysis_results.py"
Cohesion: 0.29
Nodes (4): upgrade(), _validate_columns(), upgrade(), _validate_columns()

### Community 92 - "env.py"
Cohesion: 0.48
Nodes (4): database_url(), include_object(), run_migrations_offline(), run_migrations_online()

### Community 93 - "Agent skills"
Cohesion: 0.33
Nodes (5): Agent skills, Domain docs, Issue tracker, Life-Inbox Agent Rules, Triage labels

### Community 95 - "Domain Docs"
Cohesion: 0.33
Nodes (5): Before exploring, read these, Domain Docs, File structure, Flag ADR conflicts, Use the glossary's vocabulary

### Community 96 - "Issue tracker: Local Markdown"
Cohesion: 0.33
Nodes (5): Conventions, Issue tracker: Local Markdown, Wayfinding operations, When a skill says "fetch the relevant ticket", When a skill says "publish to the issue tracker"

### Community 97 - "test_attention_board_places_tasks_in_overdue_today_and_upcoming"
Cohesion: 0.33
Nodes (4): test_attention_board_places_tasks_in_overdue_today_and_upcoming(), test_confirm_task_timing_is_preserved_on_reanalysis(), test_reanalysis_updates_task_by_source_without_changing_status(), analyze()

### Community 98 - "analyze_inbox_with_openai"
Cohesion: 0.40
Nodes (4): analyze_inbox_with_openai(), test_malformed_mocked_openai_output_uses_fallback(), test_openai_failure_fallback_omits_technical_details(), test_valid_mocked_openai_output_is_validated()

### Community 99 - "applyTimelineRange"
Cohesion: 0.60
Nodes (5): applyTimelineRange(), onTimelineDateInputChange(), onTimelineSliderChange(), setTimelinePreset(), updateTimelineSliderVisual()

### Community 104 - "promptAddPin"
Cohesion: 0.83
Nodes (4): cancelAddPin(), promptAddPin(), saveInlinePin(), savePinData()

### Community 106 - "reset_calendar_adapter_for_tests"
Cohesion: 0.67
Nodes (3): reset_calendar_adapter_for_tests(), test_google_calendar_confirm_is_blocked(), test_snooze_and_calendar_preview_confirm_mock()

## Knowledge Gaps
- **22 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `3. Configure and migrate the database` connect `3. Configure and migrate the database` to `privacy.py`, `server.py`, `Life Inbox`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Should `main.py` be split into smaller, more focused modules?**
  _Cohesion score 0.058177278401997504 - nodes in this community are weakly interconnected._
- **Why does `ask_ai()` connect `privacy.py` to `analyze_inbox_with_openai`, `test_privacy.py`, `dates.py`, `ask.py`, `test_item_kinds.py`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Should `database.py` be split into smaller, more focused modules?**
  _Cohesion score 0.04336468129571578 - nodes in this community are weakly interconnected._
- **Why does `Privacy layer` connect `privacy.py` to `3. Configure and migrate the database`?**
  _High betweenness centrality (0.035) - this node is a cross-community bridge._
- **Should `system.py` be split into smaller, more focused modules?**
  _Cohesion score 0.050239234449760764 - nodes in this community are weakly interconnected._
- **Should `typing` be split into smaller, more focused modules?**
  _Cohesion score 0.05662862159789289 - nodes in this community are weakly interconnected._