--
-- PostgreSQL database dump
--

\restrict jRFH8isCbGlVw27RdBw6ucAcVCH3EtujX4xQ2wQPNa3DDgHD22hpD7v2ODwPM1N

-- Dumped from database version 17.11 (Debian 17.11-1.pgdg13+2)
-- Dumped by pg_dump version 17.11 (Debian 17.11-1.pgdg13+2)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: enforce_voucher_balance(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.enforce_voucher_balance() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE target_id bigint; debits numeric(14,2); credits numeric(14,2);
BEGIN
    IF TG_TABLE_NAME = 'vouchers' THEN
        IF TG_OP = 'DELETE' THEN target_id := OLD.id; ELSE target_id := NEW.id; END IF;
    ELSE
        IF TG_OP = 'DELETE' THEN target_id := OLD.voucher_id;
        ELSE target_id := NEW.voucher_id;
        END IF;
    END IF;
    IF EXISTS (SELECT 1 FROM vouchers WHERE id=target_id AND status IN ('posted','reversed')) THEN
        SELECT COALESCE(sum(debit),0),COALESCE(sum(credit),0)
          INTO debits,credits FROM voucher_entries WHERE voucher_id=target_id;
        IF debits = 0 OR debits <> credits THEN
            RAISE EXCEPTION 'voucher % is not balanced: debit %, credit %',target_id,debits,credits;
        END IF;
    END IF;
    RETURN NULL;
END $$;


--
-- Name: guard_accounting_period(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.guard_accounting_period() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE expected_end date;
BEGIN
    IF NEW.period_start <> date_trunc('month',NEW.period_start)::date THEN
        RAISE EXCEPTION 'accounting period must start on the first day of a month'
            USING ERRCODE = 'check_violation';
    END IF;
    expected_end := (NEW.period_start + interval '1 month - 1 day')::date;
    IF NEW.period_end <> expected_end THEN
        RAISE EXCEPTION 'accounting period must end on %', expected_end
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM accounting_periods p
        WHERE p.id <> COALESCE(NEW.id,0)
          AND daterange(p.period_start,p.period_end,'[]') &&
              daterange(NEW.period_start,NEW.period_end,'[]')
    ) THEN
        RAISE EXCEPTION 'accounting period overlaps an existing period'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END $$;


--
-- Name: guard_posted_invoice(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.guard_posted_invoice() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE recognition_exists boolean;
BEGIN
    SELECT EXISTS(
        SELECT 1 FROM posting_events
        WHERE source_type='invoice' AND source_id=OLD.id
          AND event_type='invoice.confirmed'
    ) INTO recognition_exists;
    IF NOT recognition_exists THEN
        RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'posted invoice cannot be deleted' USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NEW.invoice_number IS DISTINCT FROM OLD.invoice_number OR
       NEW.client_id IS DISTINCT FROM OLD.client_id OR
       NEW.issue_date IS DISTINCT FROM OLD.issue_date OR
       NEW.currency IS DISTINCT FROM OLD.currency THEN
        RAISE EXCEPTION 'posted invoice accounting fields are immutable'
            USING ERRCODE='integrity_constraint_violation';
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (OLD.status='completed' AND NEW.status='void' AND EXISTS(
            SELECT 1 FROM posting_events
            WHERE source_type='invoice' AND source_id=OLD.id
              AND event_type='invoice.voided'
        )) THEN
            RAISE EXCEPTION 'illegal posted invoice status transition'
                USING ERRCODE='integrity_constraint_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;


--
-- Name: guard_posted_invoice_item(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.guard_posted_invoice_item() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE target_invoice_id bigint;
BEGIN
    target_invoice_id := CASE WHEN TG_OP='INSERT' THEN NEW.invoice_id ELSE OLD.invoice_id END;
    IF EXISTS(
        SELECT 1 FROM posting_events
        WHERE source_type='invoice' AND source_id=target_invoice_id
          AND event_type='invoice.confirmed'
    ) THEN
        RAISE EXCEPTION 'posted invoice items are immutable'
            USING ERRCODE='integrity_constraint_violation';
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;


--
-- Name: guard_voucher_mutation(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.guard_voucher_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' AND OLD.status IN ('posted','reversed') THEN
        RAISE EXCEPTION 'posted or reversed vouchers cannot be deleted';
    END IF;
    IF TG_OP = 'UPDATE' AND OLD.status = 'posted' THEN
        IF NEW.status <> 'reversed' OR NEW.reversed_at IS NULL OR
           NEW.reversed_by IS NULL OR NEW.reason_code = '' OR
           (to_jsonb(NEW) - ARRAY['status','reversed_by','reversed_at','reason_code']) <>
           (to_jsonb(OLD) - ARRAY['status','reversed_by','reversed_at','reason_code']) THEN
            RAISE EXCEPTION 'illegal posted voucher mutation';
        END IF;
    ELSIF TG_OP = 'UPDATE' AND OLD.status = 'reversed' AND to_jsonb(NEW) <> to_jsonb(OLD) THEN
        RAISE EXCEPTION 'reversed vouchers are immutable';
    ELSIF TG_OP = 'UPDATE' AND OLD.status = 'draft' AND NEW.status NOT IN ('draft','posted') THEN
        RAISE EXCEPTION 'illegal voucher status transition: % -> %', OLD.status, NEW.status;
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;


--
-- Name: invoice_sqlite_date(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.invoice_sqlite_date(value text) RETURNS text
    LANGUAGE sql STABLE STRICT
    AS $$ SELECT left(invoice_sqlite_datetime(value),10) $$;


--
-- Name: invoice_sqlite_datetime(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.invoice_sqlite_datetime(value text) RETURNS text
    LANGUAGE plpgsql STABLE STRICT
    AS $$
BEGIN
  IF value = 'now' THEN
    RETURN to_char(statement_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS');
  END IF;
  RETURN to_char(value::timestamptz AT TIME ZONE 'UTC','YYYY-MM-DD HH24:MI:SS');
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN NULL;
END $$;


--
-- Name: invoice_sqlite_julianday(text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.invoice_sqlite_julianday(value text) RETURNS double precision
    LANGUAGE plpgsql STABLE STRICT
    AS $$
BEGIN
  RETURN extract(epoch FROM value::timestamptz)::double precision / 86400.0 + 2440587.5;
EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN NULL;
END $$;


--
-- Name: protect_accounting_opening_line(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.protect_accounting_opening_line() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
  target_cutover_id bigint;
  cutover_status text;
BEGIN
  target_cutover_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.cutover_id ELSE NEW.cutover_id END;
  SELECT status INTO cutover_status FROM accounting_opening_cutover
   WHERE id = target_cutover_id FOR UPDATE;
  IF cutover_status IS DISTINCT FROM 'draft' THEN
    RAISE EXCEPTION 'posted opening balances are immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.cutover_id IS DISTINCT FROM OLD.cutover_id THEN
    RAISE EXCEPTION 'opening line cannot move between cutovers';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;


--
-- Name: protect_posted_accounting_opening_cutover(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.protect_posted_accounting_opening_cutover() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
  IF TG_OP = 'DELETE' AND OLD.status = 'posted' THEN
    RAISE EXCEPTION 'posted opening cutover is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'posted' AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'posted opening cutover is immutable';
  END IF;
  IF TG_OP = 'UPDATE' AND OLD.status = 'draft' AND NEW.status NOT IN ('draft','posted') THEN
    RAISE EXCEPTION 'invalid opening cutover transition';
  END IF;
  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;


--
-- Name: protect_posted_voucher_child(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.protect_posted_voucher_child() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE old_voucher_id bigint; new_voucher_id bigint;
BEGIN
    old_voucher_id := CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN OLD.voucher_id ELSE NULL END;
    new_voucher_id := CASE WHEN TG_OP IN ('INSERT','UPDATE') THEN NEW.voucher_id ELSE NULL END;
    IF EXISTS (SELECT 1 FROM vouchers
               WHERE id IN (old_voucher_id,new_voucher_id) AND status='posted') THEN
        RAISE EXCEPTION 'posted voucher children are immutable';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END $$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: accounting_base_check_report; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounting_base_check_report (
    id bigint NOT NULL,
    checked_at text NOT NULL,
    schema_ok boolean NOT NULL,
    report jsonb NOT NULL,
    saved_by bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL
);


--
-- Name: accounting_base_check_report_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.accounting_base_check_report ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.accounting_base_check_report_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: accounting_opening_cutover; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounting_opening_cutover (
    id bigint NOT NULL,
    cutover_date date NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    voucher_id bigint,
    created_by bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    posted_by bigint,
    posted_at text,
    CONSTRAINT accounting_opening_cutover_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'posted'::text])))
);


--
-- Name: accounting_opening_cutover_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.accounting_opening_cutover ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.accounting_opening_cutover_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: accounting_opening_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounting_opening_lines (
    id bigint NOT NULL,
    cutover_id bigint NOT NULL,
    origin_type text NOT NULL,
    origin_id bigint NOT NULL,
    account_id bigint NOT NULL,
    amount numeric(14,2) NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    reason_code text NOT NULL,
    voucher_entry_id bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT accounting_opening_lines_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT accounting_opening_lines_currency_check CHECK ((currency = 'USD'::text)),
    CONSTRAINT accounting_opening_lines_reason_code_check CHECK ((reason_code = ANY (ARRAY['prior_period_error'::text, 'post_cutover_new'::text, 'cutover_reclassification'::text])))
);


--
-- Name: accounting_opening_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.accounting_opening_lines ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.accounting_opening_lines_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: accounting_periods; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounting_periods (
    id bigint NOT NULL,
    period_start date NOT NULL,
    period_end date NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    closed_by bigint,
    closed_at text,
    reopened_by bigint,
    reopened_at text,
    reason_code text DEFAULT ''::text NOT NULL,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT accounting_periods_check CHECK ((period_end >= period_start)),
    CONSTRAINT accounting_periods_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text])))
);


--
-- Name: accounting_periods_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.accounting_periods ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.accounting_periods_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    id bigint NOT NULL,
    account_code text NOT NULL,
    account_name text NOT NULL,
    account_type text NOT NULL,
    normal_balance text NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    is_system boolean DEFAULT true NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT accounts_account_type_check CHECK ((account_type = ANY (ARRAY['asset'::text, 'liability'::text, 'equity'::text, 'revenue'::text, 'expense'::text]))),
    CONSTRAINT accounts_check CHECK ((((account_type = ANY (ARRAY['asset'::text, 'expense'::text])) AND (normal_balance = 'debit'::text)) OR ((account_type = ANY (ARRAY['liability'::text, 'equity'::text, 'revenue'::text])) AND (normal_balance = 'credit'::text)))),
    CONSTRAINT accounts_currency_check CHECK ((currency = 'USD'::text)),
    CONSTRAINT accounts_normal_balance_check CHECK ((normal_balance = ANY (ARRAY['debit'::text, 'credit'::text])))
);


--
-- Name: accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.accounts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_daily_report_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_daily_report_actions (
    id bigint NOT NULL,
    draft_id bigint NOT NULL,
    action_id text NOT NULL,
    action_version bigint NOT NULL,
    intent text NOT NULL,
    action_payload text NOT NULL,
    ai_generated bigint DEFAULT 1 NOT NULL,
    executed_at text NOT NULL,
    executed_by bigint NOT NULL,
    result text
);


--
-- Name: ai_daily_report_actions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_daily_report_actions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_daily_report_actions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_daily_report_attachment_manifests; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_daily_report_attachment_manifests (
    id bigint NOT NULL,
    manifest_id text NOT NULL,
    draft_id bigint NOT NULL,
    draft_version bigint NOT NULL,
    service_order_id bigint NOT NULL,
    report_date text NOT NULL,
    manifest_version bigint NOT NULL,
    validation_fingerprint text NOT NULL,
    draft_data_hash text NOT NULL,
    photo_set_fingerprint text,
    status text DEFAULT 'preparing'::text NOT NULL,
    manifest_fingerprint text NOT NULL,
    expected_plan text NOT NULL,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL
);


--
-- Name: ai_daily_report_attachment_manifests_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_daily_report_attachment_manifests ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_daily_report_attachment_manifests_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_daily_report_drafts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_daily_report_drafts (
    id bigint NOT NULL,
    service_order_id bigint NOT NULL,
    report_date text NOT NULL,
    draft_data text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    draft_version bigint DEFAULT 1 NOT NULL,
    conversation_context text,
    ai_model text,
    ai_confidence double precision,
    verification_required bigint DEFAULT 0 NOT NULL,
    verification_fields text,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    saved_report_id bigint
);


--
-- Name: ai_daily_report_drafts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_daily_report_drafts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_daily_report_drafts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_daily_report_formal_commits; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_daily_report_formal_commits (
    id bigint NOT NULL,
    commit_id text NOT NULL,
    draft_id bigint NOT NULL,
    manifest_id text NOT NULL,
    draft_version bigint NOT NULL,
    validation_fingerprint text NOT NULL,
    manifest_fingerprint text NOT NULL,
    manifest_snapshot text NOT NULL,
    fields_provenance text NOT NULL,
    service_report_id bigint,
    status text DEFAULT 'committing'::text NOT NULL,
    failure_code text,
    formal_files text DEFAULT '{}'::text NOT NULL,
    created_by bigint NOT NULL,
    started_at text NOT NULL,
    committed_at text,
    updated_at text
);


--
-- Name: ai_daily_report_formal_commits_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_daily_report_formal_commits ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_daily_report_formal_commits_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_daily_report_manifest_roles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_daily_report_manifest_roles (
    id bigint NOT NULL,
    manifest_id text NOT NULL,
    source_id text NOT NULL,
    role_type text NOT NULL,
    category text NOT NULL,
    visibility text NOT NULL,
    purpose text NOT NULL,
    materialization_required bigint DEFAULT 1 NOT NULL,
    sort_order bigint DEFAULT 0 NOT NULL
);


--
-- Name: ai_daily_report_manifest_roles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_daily_report_manifest_roles ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_daily_report_manifest_roles_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_daily_report_manifest_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_daily_report_manifest_sources (
    id bigint NOT NULL,
    source_id text NOT NULL,
    manifest_id text NOT NULL,
    source_type text NOT NULL,
    source_identity text NOT NULL,
    source_photo_id text,
    source_evidence_id text,
    source_relative_path text NOT NULL,
    source_sha256 text NOT NULL,
    asset_id text,
    provider text,
    provider_content text,
    compliance_review_required bigint DEFAULT 0 NOT NULL,
    compliance_status text DEFAULT 'na'::text NOT NULL,
    compliance_reviewed_by bigint,
    compliance_reviewed_at text
);


--
-- Name: ai_daily_report_manifest_sources_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_daily_report_manifest_sources ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_daily_report_manifest_sources_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_daily_report_prepared_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_daily_report_prepared_assets (
    id bigint NOT NULL,
    asset_id text NOT NULL,
    manifest_id text NOT NULL,
    prepared_relative_path text NOT NULL,
    prepared_sha256 text NOT NULL,
    content_type text NOT NULL,
    file_size bigint NOT NULL,
    integrity_status text DEFAULT 'verified'::text NOT NULL,
    prepared_at text NOT NULL
);


--
-- Name: ai_daily_report_prepared_assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_daily_report_prepared_assets ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_daily_report_prepared_assets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: ai_photo_analysis; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_photo_analysis (
    id bigint NOT NULL,
    photo_path text NOT NULL,
    photo_hash text NOT NULL,
    analysis_model text NOT NULL,
    analysis_version bigint NOT NULL,
    classification text NOT NULL,
    sub_category text,
    confidence double precision DEFAULT 0 NOT NULL,
    description text,
    equipment_id text,
    capture_time text,
    analyzed_at text NOT NULL
);


--
-- Name: ai_photo_analysis_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ai_photo_analysis ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.ai_photo_analysis_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: asset_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_events (
    id bigint NOT NULL,
    asset_id bigint NOT NULL,
    event_type text NOT NULL,
    from_status text DEFAULT ''::text NOT NULL,
    to_status text DEFAULT ''::text NOT NULL,
    holder_id bigint,
    service_order_id bigint,
    notes text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL
);


--
-- Name: asset_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.asset_events ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.asset_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: asset_photos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.asset_photos (
    id bigint NOT NULL,
    asset_id bigint NOT NULL,
    stored_filename text NOT NULL,
    original_filename text NOT NULL,
    created_by bigint,
    created_at text NOT NULL
);


--
-- Name: asset_photos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.asset_photos ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.asset_photos_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assets (
    id bigint NOT NULL,
    asset_number text NOT NULL,
    stable_id text NOT NULL,
    name text NOT NULL,
    category text DEFAULT ''::text NOT NULL,
    acquisition_value numeric(14,2) DEFAULT 0 NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    serial_number text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'available'::text NOT NULL,
    current_holder_id bigint,
    service_order_id bigint,
    notes text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    CONSTRAINT assets_acquisition_value_check CHECK ((acquisition_value >= (0)::numeric)),
    CONSTRAINT assets_check CHECK ((((status = 'assigned'::text) AND (current_holder_id IS NOT NULL)) OR (status <> 'assigned'::text))),
    CONSTRAINT assets_status_check CHECK ((status = ANY (ARRAY['available'::text, 'assigned'::text, 'damaged'::text, 'lost'::text, 'retired'::text])))
);


--
-- Name: assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.assets ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.assets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: audit_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_logs (
    id bigint NOT NULL,
    user_id bigint,
    user_name text NOT NULL,
    action text NOT NULL,
    entity_type text NOT NULL,
    entity_id text,
    entity_label text NOT NULL,
    summary text,
    created_at text NOT NULL
);


--
-- Name: audit_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.audit_logs ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.audit_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bank_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bank_accounts (
    id bigint NOT NULL,
    account_name text NOT NULL,
    bank_name text DEFAULT ''::text NOT NULL,
    account_type text DEFAULT 'checking'::text NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    last_four text DEFAULT ''::text NOT NULL,
    opening_balance numeric(14,2) DEFAULT 0 NOT NULL,
    is_active integer DEFAULT 1 NOT NULL,
    external_account_id text,
    sync_source text DEFAULT 'manual'::text NOT NULL,
    sync_status text DEFAULT 'local'::text NOT NULL,
    last_synced_at text,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    opening_balance_date text,
    initialized_at text,
    initialized_by bigint,
    CONSTRAINT bank_accounts_is_active_check CHECK ((is_active = ANY (ARRAY[0, 1])))
);


--
-- Name: bank_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.bank_accounts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.bank_accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bank_transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bank_transactions (
    id bigint NOT NULL,
    bank_account_id bigint NOT NULL,
    transaction_date text NOT NULL,
    posted_date text,
    direction text NOT NULL,
    amount numeric(14,2) NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    reference_number text DEFAULT ''::text NOT NULL,
    external_transaction_id text,
    sync_source text DEFAULT 'manual'::text NOT NULL,
    sync_status text DEFAULT 'imported'::text NOT NULL,
    import_fingerprint text NOT NULL,
    matched_payment_order_id bigint,
    matched_by bigint,
    matched_at text,
    created_by bigint,
    created_at text NOT NULL,
    matched_batch_id bigint,
    CONSTRAINT bank_transactions_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT bank_transactions_direction_check CHECK ((direction = ANY (ARRAY['debit'::text, 'credit'::text])))
);


--
-- Name: bank_transactions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.bank_transactions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.bank_transactions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: buyers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.buyers (
    id bigint NOT NULL,
    buyer_number text NOT NULL,
    country text,
    name text NOT NULL,
    contact_name text,
    contact_details text,
    detailed_address text,
    equipment_manufacturer text,
    latitude double precision,
    longitude double precision,
    geocode_address text,
    geocode_status text DEFAULT 'pending'::text NOT NULL,
    geocode_attempted_at text,
    geocode_version text,
    created_at text NOT NULL,
    client_id bigint,
    owner text,
    owner_id bigint,
    email text,
    site_size text,
    state_code text NOT NULL DEFAULT '',
    country_code text DEFAULT 'US'::text NOT NULL,
    manufacturer_id bigint,
    manual_coordinates bigint DEFAULT 0 NOT NULL
);


--
-- Name: buyers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.buyers ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.buyers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: clients; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clients (
    id bigint NOT NULL,
    client_number text NOT NULL,
    name text NOT NULL,
    short_name text NOT NULL,
    contact_name text,
    email text,
    address text,
    country text DEFAULT 'China'::text NOT NULL,
    created_at text NOT NULL,
    payment_term_id bigint
);


--
-- Name: clients_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.clients ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.clients_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: clock_in_photos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.clock_in_photos (
    id bigint NOT NULL,
    buyer_id bigint NOT NULL,
    user_id bigint NOT NULL,
    captured_at text NOT NULL,
    server_received_at text NOT NULL,
    time_offset_minutes bigint DEFAULT 0 NOT NULL,
    latitude double precision NOT NULL,
    longitude double precision NOT NULL,
    location_accuracy double precision,
    relative_path text NOT NULL,
    original_filename text NOT NULL
);


--
-- Name: clock_in_photos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.clock_in_photos ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.clock_in_photos_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: commercial_document_rates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commercial_document_rates (
    id bigint NOT NULL,
    commercial_document_id bigint NOT NULL,
    rate_type text NOT NULL,
    unit text NOT NULL,
    rate double precision DEFAULT 0 NOT NULL,
    billing_model text DEFAULT 'rate'::text NOT NULL
);


--
-- Name: commercial_document_rates_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.commercial_document_rates ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.commercial_document_rates_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: commercial_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.commercial_documents (
    id bigint NOT NULL,
    doc_type text NOT NULL,
    doc_ref_id bigint NOT NULL,
    client_id bigint,
    currency text DEFAULT 'USD'::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    driving_billing_mode text DEFAULT 'mileage_only'::text NOT NULL,
    settlement_mode text DEFAULT 'actual'::text NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    CONSTRAINT commercial_documents_doc_type_check CHECK ((doc_type = ANY (ARRAY['contract'::text, 'quotation'::text]))),
    CONSTRAINT commercial_documents_driving_billing_mode_check CHECK ((driving_billing_mode = ANY (ARRAY['mileage_only'::text, 'time_only'::text, 'mileage_and_time'::text]))),
    CONSTRAINT commercial_documents_settlement_mode_check CHECK ((settlement_mode = ANY (ARRAY['actual'::text, 'fixed'::text])))
);


--
-- Name: commercial_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.commercial_documents ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.commercial_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: company_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.company_attachments (
    id bigint NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    content_type text,
    uploaded_by bigint NOT NULL,
    uploaded_at text NOT NULL
);


--
-- Name: company_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.company_attachments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.company_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: contract_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contract_attachments (
    id bigint NOT NULL,
    contract_id bigint NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    content_type text,
    uploaded_by bigint NOT NULL,
    uploaded_at text NOT NULL
);


--
-- Name: contract_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.contract_attachments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.contract_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: contract_rate_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contract_rate_items (
    id bigint NOT NULL,
    version_id bigint NOT NULL,
    rate_type text NOT NULL,
    unit text NOT NULL,
    rate double precision DEFAULT 0 NOT NULL,
    billing_model text DEFAULT 'rate'::text NOT NULL
);


--
-- Name: contract_rate_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.contract_rate_items ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.contract_rate_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: contract_rate_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contract_rate_versions (
    id bigint NOT NULL,
    contract_id bigint NOT NULL,
    version_no bigint NOT NULL,
    effective_from text NOT NULL,
    effective_to text,
    status text DEFAULT 'active'::text NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL
);


--
-- Name: contract_rate_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.contract_rate_versions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.contract_rate_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: contracts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.contracts (
    id bigint NOT NULL,
    contract_number text NOT NULL,
    client_id bigint NOT NULL,
    contract_type text NOT NULL,
    title text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    signed_date text,
    start_date text,
    end_date text,
    currency text DEFAULT 'USD'::text NOT NULL,
    amount double precision,
    payment_terms text,
    rate_card text,
    project_name text,
    notes text,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    driving_billing_mode text DEFAULT 'mileage_only'::text NOT NULL,
    settlement_mode text DEFAULT 'actual'::text NOT NULL,
    CONSTRAINT contracts_driving_billing_mode_check CHECK ((driving_billing_mode = ANY (ARRAY['mileage_only'::text, 'time_only'::text, 'mileage_and_time'::text]))),
    CONSTRAINT contracts_settlement_mode_check CHECK ((settlement_mode = ANY (ARRAY['actual'::text, 'fixed'::text])))
);


--
-- Name: contracts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.contracts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.contracts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: countries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.countries (
    code text NOT NULL,
    region_code text NOT NULL,
    is_active bigint DEFAULT 1 NOT NULL,
    sort_order bigint DEFAULT 0 NOT NULL,
    created_at text NOT NULL
);


--
-- Name: country_translations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.country_translations (
    country_code text NOT NULL,
    language_code text NOT NULL,
    name text NOT NULL,
    region_name text NOT NULL
);


--
-- Name: customer_prepayment_applications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_prepayment_applications (
    id bigint NOT NULL,
    prepayment_id bigint NOT NULL,
    invoice_id bigint NOT NULL,
    amount numeric(14,2) NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    occurrence_no integer NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    posting_event_id bigint,
    voucher_id bigint,
    created_by bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT customer_prepayment_applications_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT customer_prepayment_applications_currency_check CHECK ((currency = 'USD'::text)),
    CONSTRAINT customer_prepayment_applications_occurrence_no_check CHECK ((occurrence_no >= 1)),
    CONSTRAINT customer_prepayment_applications_status_check CHECK ((status = ANY (ARRAY['active'::text, 'reversed'::text])))
);


--
-- Name: customer_prepayment_applications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_prepayment_applications ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.customer_prepayment_applications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_prepayments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_prepayments (
    id bigint NOT NULL,
    customer_id bigint NOT NULL,
    receipt_id bigint NOT NULL,
    original_amount numeric(14,2) NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    reason_code text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    posting_event_id bigint,
    voucher_id bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT customer_prepayments_currency_check CHECK ((currency = 'USD'::text)),
    CONSTRAINT customer_prepayments_original_amount_check CHECK ((original_amount > (0)::numeric)),
    CONSTRAINT customer_prepayments_reason_code_check CHECK ((reason_code <> ''::text)),
    CONSTRAINT customer_prepayments_status_check CHECK ((status = ANY (ARRAY['open'::text, 'applied'::text, 'reversed'::text])))
);


--
-- Name: customer_prepayments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_prepayments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.customer_prepayments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_receipts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_receipts (
    id bigint NOT NULL,
    receipt_no text NOT NULL,
    customer_id bigint NOT NULL,
    receipt_date date NOT NULL,
    amount numeric(14,2) NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    bank_account_id bigint NOT NULL,
    reason_code text DEFAULT ''::text NOT NULL,
    posting_event_id bigint,
    voucher_id bigint,
    created_by bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT customer_receipts_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT customer_receipts_currency_check CHECK ((currency = 'USD'::text))
);


--
-- Name: customer_receipts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_receipts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.customer_receipts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_reimbursement_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_reimbursement_attachments (
    id bigint NOT NULL,
    customer_reimbursement_id bigint NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    content_type text,
    uploaded_by bigint NOT NULL,
    uploaded_at text NOT NULL,
    source_expense_attachment_id bigint
);


--
-- Name: customer_reimbursement_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_reimbursement_attachments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.customer_reimbursement_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_reimbursement_expense_ignores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_reimbursement_expense_ignores (
    id bigint NOT NULL,
    customer_reimbursement_id bigint NOT NULL,
    expense_item_id bigint NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    ignored_by bigint,
    ignored_at text NOT NULL
);


--
-- Name: customer_reimbursement_expense_ignores_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_reimbursement_expense_ignores ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.customer_reimbursement_expense_ignores_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_reimbursement_expense_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_reimbursement_expense_links (
    id bigint NOT NULL,
    customer_reimbursement_id bigint NOT NULL,
    expense_item_id bigint NOT NULL,
    amount_snapshot double precision DEFAULT 0 NOT NULL,
    project_snapshot text DEFAULT ''::text NOT NULL,
    expense_status_snapshot text DEFAULT ''::text NOT NULL,
    selected_by bigint,
    selected_at text NOT NULL
);


--
-- Name: customer_reimbursement_expense_links_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_reimbursement_expense_links ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.customer_reimbursement_expense_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_reimbursement_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_reimbursement_items (
    id bigint NOT NULL,
    customer_reimbursement_id bigint NOT NULL,
    worker_name text NOT NULL,
    project_date text NOT NULL,
    standard_hours double precision DEFAULT 0 NOT NULL,
    transport_hours double precision DEFAULT 0 NOT NULL,
    overtime_hours double precision DEFAULT 0 NOT NULL,
    holiday_hours double precision DEFAULT 0 NOT NULL,
    standard_rate double precision DEFAULT 0 NOT NULL,
    transport_rate double precision DEFAULT 0 NOT NULL,
    overtime_rate double precision DEFAULT 0 NOT NULL,
    holiday_rate double precision DEFAULT 0 NOT NULL,
    labor_total double precision DEFAULT 0 NOT NULL,
    lodging double precision DEFAULT 0 NOT NULL,
    airfare double precision DEFAULT 0 NOT NULL,
    baggage double precision DEFAULT 0 NOT NULL,
    rental_car double precision DEFAULT 0 NOT NULL,
    fuel double precision DEFAULT 0 NOT NULL,
    parking double precision DEFAULT 0 NOT NULL,
    taxi double precision DEFAULT 0 NOT NULL,
    miles double precision DEFAULT 0 NOT NULL,
    mileage_rate double precision DEFAULT 0 NOT NULL,
    mileage_total double precision DEFAULT 0 NOT NULL,
    other double precision DEFAULT 0 NOT NULL,
    total double precision DEFAULT 0 NOT NULL,
    sort_order bigint DEFAULT 0 NOT NULL,
    auto_lodging double precision DEFAULT 0 NOT NULL,
    auto_airfare double precision DEFAULT 0 NOT NULL,
    auto_baggage double precision DEFAULT 0 NOT NULL,
    auto_rental_car double precision DEFAULT 0 NOT NULL,
    auto_fuel double precision DEFAULT 0 NOT NULL,
    auto_parking double precision DEFAULT 0 NOT NULL,
    auto_taxi double precision DEFAULT 0 NOT NULL,
    auto_other double precision DEFAULT 0 NOT NULL,
    source_report_id bigint,
    source_worker_user_id bigint,
    auto_expense_sources text DEFAULT '{}'::text NOT NULL,
    public_transport_hours double precision DEFAULT 0 NOT NULL,
    public_transport_rate double precision DEFAULT 0 NOT NULL
);


--
-- Name: customer_reimbursement_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_reimbursement_items ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.customer_reimbursement_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: customer_reimbursements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.customer_reimbursements (
    id bigint NOT NULL,
    service_order_id bigint NOT NULL,
    file_name text NOT NULL,
    stored_filename text NOT NULL,
    labor_total double precision DEFAULT 0 NOT NULL,
    lodging_total double precision DEFAULT 0 NOT NULL,
    travel_total double precision DEFAULT 0 NOT NULL,
    mileage_total double precision DEFAULT 0 NOT NULL,
    total_amount double precision DEFAULT 0 NOT NULL,
    invoice_id bigint,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    return_reason text,
    reviewed_by bigint,
    reviewed_at text,
    mro_supplies_total double precision DEFAULT 0 NOT NULL,
    rental_fuel_total double precision DEFAULT 0 NOT NULL,
    expense_transfer_cutoff_at text,
    contract_rate_version_id bigint,
    lodging_person_nights bigint DEFAULT 0 NOT NULL,
    lodging_cap_rate_snapshot double precision DEFAULT 0 NOT NULL,
    expense_selection_mode text DEFAULT 'legacy'::text NOT NULL,
    settlement_basis text DEFAULT 'contract_actual'::text NOT NULL,
    quotation_revision_id bigint,
    other_total double precision DEFAULT 0 NOT NULL,
    gross_amount double precision DEFAULT 0 NOT NULL,
    discount_amount double precision DEFAULT 0 NOT NULL
);


--
-- Name: customer_reimbursements_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.customer_reimbursements ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.customer_reimbursements_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: data_repair_archive; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.data_repair_archive (
    id bigint NOT NULL,
    repair_key text NOT NULL,
    table_name text NOT NULL,
    source_rowid bigint NOT NULL,
    original_json text NOT NULL,
    replacement_json text,
    reason text NOT NULL,
    repaired_at text NOT NULL
);


--
-- Name: data_repair_archive_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.data_repair_archive ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.data_repair_archive_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: email_delivery_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.email_delivery_logs (
    id bigint NOT NULL,
    entity_type text NOT NULL,
    entity_id bigint NOT NULL,
    recipient text DEFAULT ''::text NOT NULL,
    subject text DEFAULT ''::text NOT NULL,
    sent_by bigint,
    sent_by_name text DEFAULT ''::text NOT NULL,
    sent_at text NOT NULL,
    is_legacy bigint DEFAULT 0 NOT NULL,
    source_audit_log_id bigint,
    status text DEFAULT 'sent'::text NOT NULL,
    error_message text DEFAULT ''::text NOT NULL,
    employee_id integer
);


--
-- Name: email_delivery_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.email_delivery_logs ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.email_delivery_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_advance_applications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_advance_applications (
    id bigint NOT NULL,
    advance_id bigint NOT NULL,
    payment_order_id bigint NOT NULL,
    entry_type text NOT NULL,
    amount numeric(14,2) NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    posting_event_id bigint,
    voucher_id bigint,
    occurrence_no integer,
    legacy_anchor boolean DEFAULT false NOT NULL,
    CONSTRAINT employee_advance_applications_amount_check CHECK ((amount <> (0)::numeric)),
    CONSTRAINT employee_advance_applications_entry_type_check CHECK ((entry_type = ANY (ARRAY['application'::text, 'reversal'::text])))
);


--
-- Name: employee_advance_applications_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_advance_applications ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_advance_applications_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_advances; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_advances (
    id bigint NOT NULL,
    advance_number text NOT NULL,
    employee_id bigint NOT NULL,
    principal_amount numeric(14,2) NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    advance_date text NOT NULL,
    bank_account_id bigint,
    purpose text DEFAULT ''::text NOT NULL,
    attachment_name text DEFAULT ''::text NOT NULL,
    attachment_stored_filename text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    external_transaction_id text,
    sync_source text DEFAULT 'manual'::text NOT NULL,
    sync_status text DEFAULT 'local'::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    CONSTRAINT employee_advances_principal_amount_check CHECK ((principal_amount > (0)::numeric)),
    CONSTRAINT employee_advances_status_check CHECK ((status = ANY (ARRAY['open'::text, 'settled'::text, 'cancelled'::text])))
);


--
-- Name: employee_advances_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_advances ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_advances_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_grades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_grades (
    id bigint NOT NULL,
    grade_name text NOT NULL,
    base_salary double precision DEFAULT 0 NOT NULL,
    standard_hourly_rate double precision,
    transport_hourly_rate double precision,
    overtime_hourly_rate double precision,
    holiday_hourly_rate double precision,
    is_active bigint DEFAULT 1 NOT NULL,
    created_at text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    meal_daily_amount double precision DEFAULT 0 NOT NULL,
    car_allowance_method text DEFAULT 'mileage'::text NOT NULL,
    car_mileage_rate double precision,
    car_hourly_rate double precision,
    rental_driving_hourly_rate double precision
);


--
-- Name: employee_grades_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_grades ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_grades_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_payment_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_payment_batches (
    id bigint NOT NULL,
    batch_number text NOT NULL,
    employee_id bigint NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    bank_account_id bigint,
    payment_method text DEFAULT 'check'::text NOT NULL,
    check_number text DEFAULT ''::text NOT NULL,
    total_amount numeric(14,2) DEFAULT 0 NOT NULL,
    payment_count integer DEFAULT 0 NOT NULL,
    status text DEFAULT 'issued'::text NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    issued_by bigint,
    issued_at text,
    reconciled_by bigint,
    reconciled_at text,
    voided_by bigint,
    voided_at text,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    CONSTRAINT employee_payment_batches_payment_count_check CHECK ((payment_count >= 0)),
    CONSTRAINT employee_payment_batches_status_check CHECK ((status = ANY (ARRAY['issued'::text, 'reconciled'::text, 'void'::text]))),
    CONSTRAINT employee_payment_batches_total_amount_check CHECK ((total_amount >= (0)::numeric))
);


--
-- Name: employee_payment_batches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_payment_batches ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_payment_batches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_payment_components; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_payment_components (
    id bigint NOT NULL,
    payment_order_id bigint NOT NULL,
    employee_id bigint NOT NULL,
    component_code text NOT NULL,
    component_name text DEFAULT ''::text NOT NULL,
    amount numeric(14,2) NOT NULL,
    quantity numeric(14,2),
    unit text,
    unit_rate numeric(14,4),
    service_date text,
    work_order_id bigint,
    source_type text NOT NULL,
    source_id bigint,
    daily_report_id bigint,
    tax_category text NOT NULL,
    tax_status_snapshot text,
    substantiated boolean,
    review_status text DEFAULT 'confirmed'::text NOT NULL,
    created_at text NOT NULL,
    superseded_at text,
    CONSTRAINT employee_payment_components_review_status_check CHECK ((review_status = ANY (ARRAY['confirmed'::text, 'review_required'::text]))),
    CONSTRAINT employee_payment_components_service_date_check CHECK (((service_date IS NULL) OR (service_date ~ '^\d{4}-\d{2}-\d{2}'::text))),
    CONSTRAINT employee_payment_components_tax_category_check CHECK ((tax_category = ANY (ARRAY['taxable_compensation'::text, 'accountable_reimbursement'::text, 'tax_review_required'::text]))),
    CONSTRAINT employee_payment_components_tax_status_snapshot_check CHECK (((tax_status_snapshot IS NULL) OR (tax_status_snapshot = ANY (ARRAY['W2'::text, '1099'::text]))))
);


--
-- Name: employee_payment_components_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_payment_components ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_payment_components_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_payment_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_payment_orders (
    id bigint NOT NULL,
    payment_number text NOT NULL,
    employee_id bigint NOT NULL,
    payment_type text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    gross_amount numeric(14,2) NOT NULL,
    advance_offset numeric(14,2) DEFAULT 0 NOT NULL,
    other_adjustment numeric(14,2) DEFAULT 0 NOT NULL,
    net_amount numeric(14,2) NOT NULL,
    primary_advance_id bigint,
    bank_account_id bigint,
    payment_method text,
    description text DEFAULT ''::text NOT NULL,
    source_type text NOT NULL,
    source_id bigint,
    source_number text DEFAULT ''::text NOT NULL,
    source_key text,
    external_transaction_id text,
    sync_source text DEFAULT 'manual'::text NOT NULL,
    sync_status text DEFAULT 'local'::text NOT NULL,
    reviewed_by bigint,
    reviewed_at text,
    paid_by bigint,
    paid_at text,
    reconciled_by bigint,
    reconciled_at text,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    batch_id bigint,
    taxable_compensation_total numeric(14,2) DEFAULT 0 NOT NULL,
    accountable_reimbursement_total numeric(14,2) DEFAULT 0 NOT NULL,
    tax_review_required_total numeric(14,2) DEFAULT 0 NOT NULL,
    correction_status text DEFAULT 'none'::text NOT NULL,
    correction_group_id bigint,
    superseded_by_id bigint,
    correction_reason text DEFAULT ''::text NOT NULL,
    CONSTRAINT ck_employee_payments_correction_status CHECK ((correction_status = ANY (ARRAY['none'::text, 'correction_pending'::text, 'partially_superseded'::text, 'superseded'::text, 'replacement'::text, 'period_label_corrected'::text]))),
    CONSTRAINT employee_payment_orders_advance_offset_check CHECK ((advance_offset >= (0)::numeric)),
    CONSTRAINT employee_payment_orders_check CHECK ((net_amount = ((gross_amount - advance_offset) + other_adjustment))),
    CONSTRAINT employee_payment_orders_gross_amount_check CHECK ((gross_amount >= (0)::numeric)),
    CONSTRAINT employee_payment_orders_net_amount_check CHECK ((net_amount >= (0)::numeric)),
    CONSTRAINT employee_payment_orders_payment_type_check CHECK ((payment_type = ANY (ARRAY['salary'::text, 'expense'::text]))),
    CONSTRAINT employee_payment_orders_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'pending_review'::text, 'approved'::text, 'pending_payment'::text, 'paid'::text, 'reconciled'::text, 'rejected'::text, 'cancelled'::text, 'payment_failed'::text])))
);


--
-- Name: employee_payment_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_payment_orders ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_payment_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_payment_tax_reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_payment_tax_reviews (
    id bigint NOT NULL,
    component_id bigint NOT NULL,
    reviewed_by bigint NOT NULL,
    reviewed_at text NOT NULL,
    previous_tax_category text,
    new_tax_category text NOT NULL,
    reason text NOT NULL,
    CONSTRAINT employee_payment_tax_reviews_new_tax_category_check CHECK ((new_tax_category = ANY (ARRAY['taxable_compensation'::text, 'accountable_reimbursement'::text, 'tax_review_required'::text]))),
    CONSTRAINT employee_payment_tax_reviews_previous_tax_category_check CHECK (((previous_tax_category IS NULL) OR (previous_tax_category = ANY (ARRAY['taxable_compensation'::text, 'accountable_reimbursement'::text, 'tax_review_required'::text])))),
    CONSTRAINT employee_payment_tax_reviews_reason_check CHECK ((btrim(reason) <> ''::text))
);


--
-- Name: employee_payment_tax_reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_payment_tax_reviews ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_payment_tax_reviews_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_rate_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_rate_items (
    id bigint NOT NULL,
    version_id bigint NOT NULL,
    rate_type text NOT NULL,
    unit text NOT NULL,
    rate double precision DEFAULT 0 NOT NULL
);


--
-- Name: employee_rate_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_rate_items ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_rate_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_rate_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_rate_versions (
    id bigint NOT NULL,
    employee_grade_id bigint NOT NULL,
    version_no bigint NOT NULL,
    effective_from text NOT NULL,
    effective_to text,
    status text DEFAULT 'active'::text NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL
);


--
-- Name: employee_rate_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_rate_versions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_rate_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: employee_salary_agreements; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.employee_salary_agreements (
    id bigint NOT NULL,
    employee_id bigint NOT NULL,
    amount numeric(14,2) NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    effective_from text NOT NULL,
    effective_to text,
    replaces_system_payroll integer DEFAULT 1 NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    is_active integer DEFAULT 1 NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    CONSTRAINT employee_salary_agreements_amount_check CHECK ((amount >= (0)::numeric)),
    CONSTRAINT employee_salary_agreements_check CHECK (((effective_to IS NULL) OR (effective_to >= effective_from))),
    CONSTRAINT employee_salary_agreements_is_active_check CHECK ((is_active = ANY (ARRAY[0, 1]))),
    CONSTRAINT employee_salary_agreements_replaces_system_payroll_check CHECK ((replaces_system_payroll = ANY (ARRAY[0, 1])))
);


--
-- Name: employee_salary_agreements_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.employee_salary_agreements ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.employee_salary_agreements_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: expense_ai_reviews; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.expense_ai_reviews (
    id bigint NOT NULL,
    expense_id integer NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    conclusion text DEFAULT ''::text NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    error text DEFAULT ''::text NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL
);


--
-- Name: expense_ai_reviews_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.expense_ai_reviews_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: expense_ai_reviews_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.expense_ai_reviews_id_seq OWNED BY public.expense_ai_reviews.id;


--
-- Name: expense_attachment_interpretations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.expense_attachment_interpretations (
    id bigint NOT NULL,
    attachment_id integer NOT NULL,
    model text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    content text DEFAULT ''::text NOT NULL,
    error text DEFAULT ''::text NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL
);


--
-- Name: expense_attachment_interpretations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.expense_attachment_interpretations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: expense_attachment_interpretations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.expense_attachment_interpretations_id_seq OWNED BY public.expense_attachment_interpretations.id;


--
-- Name: expense_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.expense_attachments (
    id bigint NOT NULL,
    expense_id bigint NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    content_type text,
    uploaded_by bigint NOT NULL,
    uploaded_at text NOT NULL,
    expense_item_key text,
    file_sha256 text,
    image_dhash text
);


--
-- Name: expense_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.expense_attachments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.expense_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: expense_duplicate_checks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.expense_duplicate_checks (
    id bigint NOT NULL,
    expense_id bigint NOT NULL,
    attachment_id bigint NOT NULL,
    matched_expense_id bigint NOT NULL,
    matched_attachment_id bigint NOT NULL,
    risk_level text NOT NULL,
    score bigint DEFAULT 0 NOT NULL,
    reasons text DEFAULT ''::text NOT NULL,
    deepseek_analysis text,
    deepseek_error text,
    review_status text DEFAULT 'pending'::text NOT NULL,
    reviewed_by bigint,
    reviewed_at text,
    created_at text NOT NULL,
    updated_at text NOT NULL
);


--
-- Name: expense_duplicate_checks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.expense_duplicate_checks ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.expense_duplicate_checks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: expense_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.expense_items (
    id bigint NOT NULL,
    expense_id bigint NOT NULL,
    project_id bigint NOT NULL,
    project text NOT NULL,
    amount double precision DEFAULT 0 NOT NULL,
    description text,
    sort_order bigint DEFAULT 0 NOT NULL,
    fuel_vehicle_type text,
    line_key text,
    tax_category text,
    substantiated boolean,
    CONSTRAINT expense_items_tax_category_check CHECK (((tax_category IS NULL) OR (tax_category = ANY (ARRAY['taxable_compensation'::text, 'accountable_reimbursement'::text, 'tax_review_required'::text]))))
);


--
-- Name: expense_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.expense_items ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.expense_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: expense_save_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.expense_save_tokens (
    token text NOT NULL,
    expense_id bigint,
    created_at text NOT NULL
);


--
-- Name: expense_settlement_invoice_map; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.expense_settlement_invoice_map (
    id bigint NOT NULL,
    expense_project_name text NOT NULL,
    settlement_field text NOT NULL,
    invoice_project_name text NOT NULL,
    created_at text NOT NULL
);


--
-- Name: expense_settlement_invoice_map_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.expense_settlement_invoice_map_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: expense_settlement_invoice_map_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.expense_settlement_invoice_map_id_seq OWNED BY public.expense_settlement_invoice_map.id;


--
-- Name: expenses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.expenses (
    id bigint NOT NULL,
    service_order_id bigint NOT NULL,
    expense_number text NOT NULL,
    project text NOT NULL,
    expense_date text NOT NULL,
    amount double precision DEFAULT 0 NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    description text,
    status text DEFAULT 'draft'::text NOT NULL,
    return_reason text,
    reviewed_by bigint,
    reviewed_at text,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    project_id bigint,
    payout_status text DEFAULT 'pending'::text NOT NULL,
    reimbursed_by bigint,
    reimbursed_at text,
    beneficiary_id bigint,
    business_purpose text,
    substantiated boolean,
    tax_category text,
    CONSTRAINT expenses_tax_category_check CHECK (((tax_category IS NULL) OR (tax_category = ANY (ARRAY['taxable_compensation'::text, 'accountable_reimbursement'::text, 'tax_review_required'::text]))))
);


--
-- Name: expenses_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.expenses ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.expenses_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: field_photos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.field_photos (
    id bigint NOT NULL,
    client_id text NOT NULL,
    order_id bigint NOT NULL,
    user_id bigint NOT NULL,
    captured_at text NOT NULL,
    received_at text NOT NULL,
    capture_date text NOT NULL,
    timezone_name text NOT NULL,
    latitude double precision NOT NULL,
    longitude double precision NOT NULL,
    accuracy double precision NOT NULL,
    location_note text DEFAULT ''::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    source text NOT NULL,
    relative_path text NOT NULL,
    content_hash text NOT NULL,
    bytes bigint NOT NULL,
    equipment_number text DEFAULT ''::text NOT NULL,
    position_number text DEFAULT ''::text NOT NULL,
    equipment_session text DEFAULT ''::text NOT NULL,
    photo_type text DEFAULT ''::text NOT NULL,
    watermark_at text DEFAULT ''::text NOT NULL,
    batch_id text DEFAULT ''::text NOT NULL,
    container_number text DEFAULT ''::text NOT NULL,
    technician_name text DEFAULT ''::text NOT NULL,
    technician_user_id bigint,
    pump_fuse_numbers text DEFAULT ''::text NOT NULL,
    watermark_source text DEFAULT ''::text NOT NULL,
    location_verified bigint DEFAULT 1 NOT NULL,
    capture_date_source text DEFAULT ''::text NOT NULL
);


--
-- Name: field_photos_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.field_photos ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.field_photos_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: invoice_accounting_corrections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_accounting_corrections (
    id bigint NOT NULL,
    invoice_id bigint NOT NULL,
    correction_no integer NOT NULL,
    accounting_date date NOT NULL,
    revenue_delta numeric(14,2) DEFAULT 0 NOT NULL,
    sales_tax_delta numeric(14,2) DEFAULT 0 NOT NULL,
    reason_code text NOT NULL,
    status text DEFAULT 'posted'::text NOT NULL,
    posting_event_id bigint,
    voucher_id bigint,
    created_by bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    reversal_event_id bigint,
    reversal_voucher_id bigint,
    reversed_by bigint,
    reversed_at text,
    reversal_reason text DEFAULT ''::text NOT NULL,
    CONSTRAINT invoice_accounting_corrections_check CHECK (((revenue_delta <> (0)::numeric) OR (sales_tax_delta <> (0)::numeric))),
    CONSTRAINT invoice_accounting_corrections_correction_no_check CHECK ((correction_no >= 1)),
    CONSTRAINT invoice_accounting_corrections_reason_code_check CHECK ((reason_code <> ''::text)),
    CONSTRAINT invoice_accounting_corrections_status_check CHECK ((status = ANY (ARRAY['posted'::text, 'reversed'::text])))
);


--
-- Name: invoice_accounting_corrections_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.invoice_accounting_corrections ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.invoice_accounting_corrections_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: invoice_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_attachments (
    id bigint NOT NULL,
    invoice_id bigint NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    content_type text,
    uploaded_by bigint NOT NULL,
    uploaded_at text NOT NULL
);


--
-- Name: invoice_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.invoice_attachments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.invoice_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: invoice_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_items (
    id bigint NOT NULL,
    invoice_id bigint NOT NULL,
    project_id bigint NOT NULL,
    description text NOT NULL,
    amount double precision NOT NULL,
    tax_rate double precision DEFAULT 0 NOT NULL
);


--
-- Name: invoice_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.invoice_items ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.invoice_items_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: invoice_save_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_save_tokens (
    token text NOT NULL,
    invoice_id bigint,
    created_at text NOT NULL
);


--
-- Name: invoice_schema_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_schema_version (
    singleton integer NOT NULL,
    version text NOT NULL,
    source_sha256 text NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT invoice_schema_version_singleton_check CHECK ((singleton = 1))
);


--
-- Name: invoice_sqlite_columns; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoice_sqlite_columns (
    table_name text NOT NULL,
    cid integer NOT NULL,
    name text NOT NULL,
    type text NOT NULL,
    "notnull" integer NOT NULL,
    dflt_value text,
    pk integer NOT NULL
);


--
-- Name: invoice_sqlite_master; Type: VIEW; Schema: public; Owner: -
--

CREATE VIEW public.invoice_sqlite_master AS
 SELECT table_name AS name,
        CASE table_type
            WHEN 'VIEW'::text THEN 'view'::text
            ELSE 'table'::text
        END AS type
   FROM information_schema.tables
  WHERE (((table_schema)::name = 'public'::name) AND ((table_name)::name <> ALL (ARRAY['invoice_schema_version'::name, 'invoice_sqlite_columns'::name, 'invoice_sqlite_master'::name])));


--
-- Name: invoices; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.invoices (
    id bigint NOT NULL,
    invoice_number text NOT NULL,
    client_id bigint NOT NULL,
    issue_date text NOT NULL,
    due_date text NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    notes text,
    status text DEFAULT 'submitted'::text NOT NULL,
    return_reason text,
    sent_at text,
    paid_at text,
    payment_amount double precision,
    payment_note text,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    service_order_id bigint
);


--
-- Name: invoices_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.invoices ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.invoices_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: knowledge_document_versions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_document_versions (
    id bigint NOT NULL,
    document_id bigint NOT NULL,
    version_number bigint NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    file_size bigint DEFAULT 0 NOT NULL,
    extracted_text text DEFAULT ''::text NOT NULL,
    change_note text DEFAULT ''::text NOT NULL,
    uploaded_by bigint,
    uploaded_at text NOT NULL
);


--
-- Name: knowledge_document_versions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.knowledge_document_versions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.knowledge_document_versions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: knowledge_documents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_documents (
    id bigint NOT NULL,
    title text NOT NULL,
    category text DEFAULT '其他'::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    content_type text DEFAULT 'application/pdf'::text NOT NULL,
    file_size bigint DEFAULT 0 NOT NULL,
    uploaded_by bigint,
    uploaded_at text NOT NULL,
    updated_at text NOT NULL,
    is_pinned bigint DEFAULT 0 NOT NULL,
    expires_on text,
    view_count bigint DEFAULT 0 NOT NULL,
    download_count bigint DEFAULT 0 NOT NULL,
    search_text text DEFAULT ''::text NOT NULL,
    text_indexed_at text,
    current_version bigint DEFAULT 1 NOT NULL
);


--
-- Name: knowledge_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.knowledge_documents ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.knowledge_documents_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: llm_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.llm_configs (
    id bigint NOT NULL,
    name text NOT NULL,
    base_url text NOT NULL,
    api_key text DEFAULT ''::text NOT NULL,
    model text NOT NULL,
    supports_vision boolean DEFAULT false NOT NULL,
    timeout_seconds integer DEFAULT 300 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL
);


--
-- Name: llm_configs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.llm_configs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: llm_configs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.llm_configs_id_seq OWNED BY public.llm_configs.id;


--
-- Name: manufacturers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.manufacturers (
    id bigint NOT NULL,
    manufacturer_number text NOT NULL,
    name text NOT NULL,
    created_at text NOT NULL
);


--
-- Name: manufacturers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.manufacturers ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.manufacturers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.messages (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    title text NOT NULL,
    body text NOT NULL,
    link text,
    is_read bigint DEFAULT 0 NOT NULL,
    created_at text NOT NULL
);


--
-- Name: messages_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.messages ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.messages_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: owners; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.owners (
    id bigint NOT NULL,
    owner_number text NOT NULL,
    name text NOT NULL,
    created_at text NOT NULL
);


--
-- Name: owners_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.owners ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.owners_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payment_order_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_order_events (
    id bigint NOT NULL,
    payment_order_id bigint NOT NULL,
    event_type text NOT NULL,
    from_status text DEFAULT ''::text NOT NULL,
    to_status text DEFAULT ''::text NOT NULL,
    details text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL
);


--
-- Name: payment_order_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.payment_order_events ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.payment_order_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payment_order_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_order_sources (
    id bigint NOT NULL,
    payment_order_id bigint NOT NULL,
    source_type text NOT NULL,
    source_id bigint,
    source_number text DEFAULT ''::text NOT NULL,
    amount numeric(14,2) NOT NULL,
    created_at text NOT NULL
);


--
-- Name: payment_order_sources_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.payment_order_sources ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.payment_order_sources_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payment_terms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payment_terms (
    id bigint NOT NULL,
    name text NOT NULL,
    rule_type text DEFAULT 'fixed_days'::text NOT NULL,
    fixed_days bigint DEFAULT 30 NOT NULL,
    cutoff_day bigint,
    due_day bigint,
    before_due_months bigint DEFAULT 1 NOT NULL,
    after_due_months bigint DEFAULT 2 NOT NULL,
    notes text,
    is_active bigint DEFAULT 1 NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL
);


--
-- Name: payment_terms_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.payment_terms ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.payment_terms_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payroll_component_correction_allocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payroll_component_correction_allocations (
    id bigint NOT NULL,
    correction_group_id bigint NOT NULL,
    component_id bigint NOT NULL,
    canonical_payment_order_id bigint,
    canonical_period_start text,
    canonical_period_end text,
    disposition text NOT NULL,
    keeper_component_id bigint,
    reason text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    CONSTRAINT ck_correction_allocation_canonical_period CHECK ((((canonical_period_start IS NULL) AND (canonical_period_end IS NULL)) OR ((canonical_period_start IS NOT NULL) AND (canonical_period_end IS NOT NULL) AND (((canonical_period_end)::date - (canonical_period_start)::date) = 13)))),
    CONSTRAINT ck_correction_allocation_keeper CHECK (
CASE disposition
    WHEN 'duplicate_superseded'::text THEN (keeper_component_id IS NOT NULL)
    ELSE (keeper_component_id IS NULL)
END),
    CONSTRAINT payroll_component_correction_allocations_disposition_check CHECK ((disposition = ANY (ARRAY['retained'::text, 'duplicate_superseded'::text, 'carry_forward'::text, 'pre_canonical_legacy'::text])))
);


--
-- Name: payroll_component_correction_allocations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.payroll_component_correction_allocations ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.payroll_component_correction_allocations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payroll_component_tax_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payroll_component_tax_config (
    id bigint NOT NULL,
    component_code text NOT NULL,
    display_name text NOT NULL,
    default_tax_category text NOT NULL,
    requires_substantiation integer DEFAULT 0 NOT NULL,
    effective_from text NOT NULL,
    effective_to text,
    is_active integer DEFAULT 1 NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    CONSTRAINT payroll_component_tax_config_check CHECK (((effective_to IS NULL) OR (effective_to >= effective_from))),
    CONSTRAINT payroll_component_tax_config_default_tax_category_check CHECK ((default_tax_category = ANY (ARRAY['taxable_compensation'::text, 'accountable_reimbursement'::text, 'tax_review_required'::text]))),
    CONSTRAINT payroll_component_tax_config_is_active_check CHECK ((is_active = ANY (ARRAY[0, 1]))),
    CONSTRAINT payroll_component_tax_config_requires_substantiation_check CHECK ((requires_substantiation = ANY (ARRAY[0, 1])))
);


--
-- Name: payroll_component_tax_config_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.payroll_component_tax_config ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.payroll_component_tax_config_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: payroll_correction_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.payroll_correction_groups (
    id bigint NOT NULL,
    correction_code text NOT NULL,
    correction_type text DEFAULT 'payroll_cycle_realignment'::text NOT NULL,
    policy_version text DEFAULT ''::text NOT NULL,
    canonical_cycle_start text,
    canonical_cycle_days integer DEFAULT 14 NOT NULL,
    canonical_period_start text,
    canonical_period_end text,
    status text DEFAULT 'draft'::text NOT NULL,
    reason text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    CONSTRAINT payroll_correction_groups_canonical_cycle_days_check CHECK ((canonical_cycle_days > 0)),
    CONSTRAINT payroll_correction_groups_correction_type_check CHECK ((correction_type = ANY (ARRAY['payroll_cycle_realignment'::text, 'other'::text]))),
    CONSTRAINT payroll_correction_groups_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'applied'::text, 'rolled_back'::text])))
);


--
-- Name: payroll_correction_groups_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.payroll_correction_groups ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.payroll_correction_groups_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: posting_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posting_audit (
    id bigint NOT NULL,
    posting_event_id bigint,
    voucher_id bigint,
    action text NOT NULL,
    old_value jsonb,
    new_value jsonb,
    reason_code text DEFAULT ''::text NOT NULL,
    actor_id bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL
);


--
-- Name: posting_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.posting_audit ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.posting_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: posting_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.posting_events (
    id bigint NOT NULL,
    source_type text NOT NULL,
    source_id bigint NOT NULL,
    source_version bigint NOT NULL,
    event_type text NOT NULL,
    occurrence_no integer NOT NULL,
    requires_gl boolean NOT NULL,
    payload_hash text NOT NULL,
    business_anchor_type text NOT NULL,
    business_anchor_id bigint NOT NULL,
    idempotency_key text,
    voucher_id bigint,
    created_by bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    related_source_id bigint DEFAULT 0 NOT NULL,
    CONSTRAINT ck_posting_events_related_source_id CHECK ((related_source_id >= 0)),
    CONSTRAINT posting_events_check CHECK (((requires_gl AND (voucher_id IS NOT NULL)) OR ((NOT requires_gl) AND (voucher_id IS NULL)))),
    CONSTRAINT posting_events_occurrence_no_check CHECK ((occurrence_no >= 1)),
    CONSTRAINT posting_events_payload_hash_check CHECK ((payload_hash ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT posting_events_source_version_check CHECK ((source_version >= 1))
);


--
-- Name: posting_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.posting_events ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.posting_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: profit_ledger; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.profit_ledger (
    id bigint NOT NULL,
    work_date text NOT NULL,
    service_order_id bigint NOT NULL,
    employee_id bigint,
    category text NOT NULL,
    item_type text NOT NULL,
    quantity double precision DEFAULT 0 NOT NULL,
    unit text DEFAULT ''::text NOT NULL,
    client_rate_snapshot double precision DEFAULT 0 NOT NULL,
    employee_rate_snapshot double precision DEFAULT 0 NOT NULL,
    revenue double precision DEFAULT 0 NOT NULL,
    cost double precision DEFAULT 0 NOT NULL,
    profit double precision DEFAULT 0 NOT NULL,
    contract_rate_version_id bigint,
    employee_rate_version_id bigint,
    source_type text NOT NULL,
    source_id bigint,
    source_line_id bigint,
    allocation_method text DEFAULT 'direct'::text NOT NULL,
    calculation_status text DEFAULT 'estimated'::text NOT NULL,
    calculation_version bigint DEFAULT 1 NOT NULL,
    created_at text NOT NULL
);


--
-- Name: profit_ledger_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.profit_ledger ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.profit_ledger_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: projects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.projects (
    id bigint NOT NULL,
    name text NOT NULL,
    default_amount double precision DEFAULT 0 NOT NULL,
    tax_rate double precision DEFAULT 0 NOT NULL,
    is_active bigint DEFAULT 1 NOT NULL,
    created_at text NOT NULL,
    project_type text DEFAULT 'invoice'::text NOT NULL,
    unit_price double precision DEFAULT 0 NOT NULL,
    name_key text
);


--
-- Name: projects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.projects ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.projects_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: quotation_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quotation_revisions (
    id bigint NOT NULL,
    quotation_id bigint NOT NULL,
    revision_no bigint NOT NULL,
    snapshot_json text NOT NULL,
    subtotal numeric(14,2) DEFAULT 0 NOT NULL,
    tax numeric(14,2) DEFAULT 0 NOT NULL,
    total numeric(14,2) DEFAULT 0 NOT NULL,
    created_by bigint NOT NULL,
    created_at text NOT NULL
);


--
-- Name: quotation_revisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.quotation_revisions ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.quotation_revisions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: quotations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.quotations (
    id bigint NOT NULL,
    quotation_number text NOT NULL,
    quotation_date text NOT NULL,
    valid_until text NOT NULL,
    prepared_by text DEFAULT ''::text NOT NULL,
    customer text DEFAULT ''::text NOT NULL,
    contact text DEFAULT ''::text NOT NULL,
    project_name text DEFAULT ''::text NOT NULL,
    project_no text DEFAULT ''::text NOT NULL,
    project_location text DEFAULT ''::text NOT NULL,
    po_no text DEFAULT ''::text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    project_description text DEFAULT ''::text NOT NULL,
    scope_1 text DEFAULT ''::text NOT NULL,
    scope_2 text DEFAULT ''::text NOT NULL,
    scope_3 text DEFAULT ''::text NOT NULL,
    pricing_lines text DEFAULT '[]'::text NOT NULL,
    subtotal numeric(14,2) DEFAULT 0 NOT NULL,
    tax numeric(14,2) DEFAULT 0 NOT NULL,
    total numeric(14,2) DEFAULT 0 NOT NULL,
    rate_schedule text DEFAULT '[]'::text NOT NULL,
    crew_size text DEFAULT ''::text NOT NULL,
    workdays text DEFAULT ''::text NOT NULL,
    hours_per_day text DEFAULT ''::text NOT NULL,
    expected_start_date text DEFAULT ''::text NOT NULL,
    expected_completion text DEFAULT ''::text NOT NULL,
    normal_working_hours text DEFAULT ''::text NOT NULL,
    customer_provides text DEFAULT ''::text NOT NULL,
    assumptions_other text DEFAULT ''::text NOT NULL,
    payment_terms text DEFAULT 'net_30'::text NOT NULL,
    payment_terms_other text DEFAULT ''::text NOT NULL,
    quotation_validity text DEFAULT '30 calendar days'::text NOT NULL,
    invoice_frequency text DEFAULT 'upon_completion'::text NOT NULL,
    pricing_type text DEFAULT 'estimated'::text NOT NULL,
    tax_note text DEFAULT 'Excluded unless stated'::text NOT NULL,
    notes text DEFAULT ''::text NOT NULL,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    client_id bigint,
    current_revision_no bigint DEFAULT 0 NOT NULL,
    gross_total numeric(14,2) DEFAULT 0 NOT NULL,
    discount_amount numeric(14,2) DEFAULT 0 NOT NULL,
    final_amount numeric(14,2) DEFAULT 0 NOT NULL,
    driving_billing_mode text DEFAULT 'mileage_only'::text NOT NULL,
    settlement_mode text DEFAULT 'actual'::text NOT NULL,
    CONSTRAINT quotations_discount_range_check CHECK (((discount_amount >= (0)::numeric) AND (discount_amount <= gross_total))),
    CONSTRAINT quotations_driving_billing_mode_check CHECK ((driving_billing_mode = ANY (ARRAY['mileage_only'::text, 'time_only'::text, 'mileage_and_time'::text]))),
    CONSTRAINT quotations_invoice_frequency_check CHECK ((invoice_frequency = ANY (ARRAY['weekly'::text, 'biweekly'::text, 'monthly'::text, 'upon_completion'::text]))),
    CONSTRAINT quotations_payment_terms_check CHECK ((payment_terms = ANY (ARRAY['net_15'::text, 'net_30'::text, 'net_45'::text, 'other'::text]))),
    CONSTRAINT quotations_pricing_type_check CHECK ((pricing_type = ANY (ARRAY['fixed'::text, 'estimated'::text]))),
    CONSTRAINT quotations_settlement_mode_check CHECK ((settlement_mode = ANY (ARRAY['actual'::text, 'fixed'::text]))),
    CONSTRAINT quotations_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'sent'::text, 'accepted'::text, 'expired'::text, 'cancelled'::text])))
);


--
-- Name: quotations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.quotations ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.quotations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: receipt_allocations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.receipt_allocations (
    id bigint NOT NULL,
    receipt_id bigint NOT NULL,
    invoice_id bigint NOT NULL,
    amount numeric(14,2) NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    reversed_by bigint,
    redirected_to bigint,
    source_event_id bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT receipt_allocations_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT receipt_allocations_check CHECK (((id IS DISTINCT FROM reversed_by) AND (id IS DISTINCT FROM redirected_to))),
    CONSTRAINT receipt_allocations_currency_check CHECK ((currency = 'USD'::text)),
    CONSTRAINT receipt_allocations_status_check CHECK ((status = ANY (ARRAY['active'::text, 'reversed'::text, 'redirected'::text])))
);


--
-- Name: receipt_allocations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.receipt_allocations ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.receipt_allocations_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: role_action_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_action_permissions (
    role text NOT NULL,
    resource_key text NOT NULL,
    action_key text NOT NULL,
    is_enabled bigint DEFAULT 1 NOT NULL,
    updated_by bigint,
    updated_at text NOT NULL
);


--
-- Name: role_menu_permissions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.role_menu_permissions (
    role text NOT NULL,
    menu_key text NOT NULL,
    is_enabled bigint DEFAULT 1 NOT NULL,
    updated_by bigint,
    updated_at text NOT NULL
);


--
-- Name: service_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_orders (
    id bigint NOT NULL,
    order_number text NOT NULL,
    client_name text NOT NULL,
    site_address text NOT NULL,
    client_order_number text NOT NULL,
    status text DEFAULT 'open'::text NOT NULL,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    latitude double precision,
    longitude double precision,
    geocode_address text,
    geocode_status text DEFAULT 'pending'::text NOT NULL,
    geocode_attempted_at text,
    geocode_version text,
    buyer_id bigint,
    buyer_contact_name text,
    buyer_contact_details text,
    start_date text,
    work_order_type_id bigint,
    client_id bigint,
    region_code text DEFAULT 'americas'::text NOT NULL,
    country_code text DEFAULT 'US'::text NOT NULL,
    contract_id bigint,
    manufacturer_id bigint,
    quotation_id bigint,
    settlement_basis text DEFAULT 'contract_actual'::text NOT NULL,
    commercial_document_id bigint,
    commercial_document_type text,
    CONSTRAINT service_orders_commercial_document_type_check CHECK ((commercial_document_type = ANY (ARRAY['contract'::text, 'quotation'::text])))
);


--
-- Name: service_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_orders ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.service_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_report_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_report_attachments (
    id bigint NOT NULL,
    report_id bigint NOT NULL,
    category text NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    content_type text,
    uploaded_by bigint NOT NULL,
    uploaded_at text NOT NULL
);


--
-- Name: service_report_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_report_attachments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.service_report_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_report_mileage_evidence; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_report_mileage_evidence (
    id bigint NOT NULL,
    report_id bigint NOT NULL,
    worker_user_id bigint NOT NULL,
    route_fingerprint text NOT NULL,
    origin_address text DEFAULT ''::text NOT NULL,
    destination_address text DEFAULT ''::text NOT NULL,
    trip_type text DEFAULT 'round_trip'::text NOT NULL,
    distance_meters double precision,
    duration_seconds bigint,
    one_way_miles double precision,
    reported_miles double precision,
    attachment_id bigint,
    status text DEFAULT 'success'::text NOT NULL,
    generated_by bigint,
    generated_at text NOT NULL
);


--
-- Name: service_report_mileage_evidence_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_report_mileage_evidence ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.service_report_mileage_evidence_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_report_replaced_parts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_report_replaced_parts (
    id bigint NOT NULL,
    report_id bigint NOT NULL,
    part_number text,
    part_name text,
    old_serial_number text,
    new_serial_number text,
    quantity text,
    sort_order bigint DEFAULT 0 NOT NULL
);


--
-- Name: service_report_replaced_parts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_report_replaced_parts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.service_report_replaced_parts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_report_save_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_report_save_tokens (
    token text NOT NULL,
    report_id bigint,
    created_at text NOT NULL
);


--
-- Name: service_report_saved_parts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_report_saved_parts (
    id bigint NOT NULL,
    report_id bigint NOT NULL,
    part_number text,
    part_name text,
    quantity text,
    status text,
    sort_order bigint DEFAULT 0 NOT NULL
);


--
-- Name: service_report_saved_parts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_report_saved_parts ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.service_report_saved_parts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_report_workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_report_workers (
    id bigint NOT NULL,
    report_id bigint NOT NULL,
    user_id bigint NOT NULL,
    driving_miles double precision DEFAULT 0 NOT NULL,
    travel_mode text DEFAULT 'legacy'::text NOT NULL,
    travel_hours double precision,
    public_transport_hours double precision,
    work_description text DEFAULT ''::text NOT NULL,
    origin_address text DEFAULT ''::text NOT NULL,
    trip_type text DEFAULT 'round_trip'::text NOT NULL
);


--
-- Name: service_report_workers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_report_workers ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.service_report_workers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: service_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.service_reports (
    id bigint NOT NULL,
    service_order_id bigint NOT NULL,
    report_date text NOT NULL,
    total_service_hours double precision DEFAULT 0 NOT NULL,
    travel_hours double precision DEFAULT 0 NOT NULL,
    public_transport_hours double precision DEFAULT 0 NOT NULL,
    driving_miles double precision DEFAULT 0 NOT NULL,
    departure_address text,
    site_address text,
    total_time text,
    cabinet_number text,
    arrival_time text,
    departure_time text,
    service_description text,
    created_by bigint NOT NULL,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    actual_work_date text,
    report_writer_id bigint,
    mileage_billing_method text DEFAULT 'per_person'::text NOT NULL,
    ai_generated bigint DEFAULT 0 NOT NULL,
    arrival_time_source text,
    departure_time_source text,
    arrival_photo_relative_path text,
    arrival_photo_hash text,
    departure_photo_relative_path text,
    departure_photo_hash text,
    ai_draft_id bigint
);


--
-- Name: service_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.service_reports ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.service_reports_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: settings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.settings (
    key text NOT NULL,
    value text NOT NULL
);


--
-- Name: user_attachments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_attachments (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    original_filename text NOT NULL,
    stored_filename text NOT NULL,
    content_type text,
    uploaded_by bigint NOT NULL,
    uploaded_at text NOT NULL
);


--
-- Name: user_attachments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.user_attachments ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.user_attachments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: user_service_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_service_orders (
    user_id bigint NOT NULL,
    service_order_id bigint NOT NULL,
    assigned_by bigint NOT NULL,
    assigned_at text NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    name text NOT NULL,
    email text NOT NULL,
    password_hash text NOT NULL,
    role text DEFAULT 'user'::text NOT NULL,
    client_id bigint,
    created_at text NOT NULL,
    is_active bigint DEFAULT 1 NOT NULL,
    region_code text DEFAULT 'americas'::text NOT NULL,
    country_code text DEFAULT 'US'::text NOT NULL,
    employee_grade_id bigint,
    address text DEFAULT ''::text NOT NULL,
    default_language text DEFAULT 'zh-CN'::text NOT NULL,
    phone text DEFAULT ''::text NOT NULL,
    phone_verified bigint DEFAULT 0 NOT NULL,
    phone_verified_at text,
    preferred_communication_language text DEFAULT 'zh-CN'::text NOT NULL,
    communication_languages text DEFAULT 'zh-CN'::text NOT NULL,
    english_name text DEFAULT ''::text NOT NULL
);


--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.users ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: voucher_attachment_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voucher_attachment_links (
    id bigint NOT NULL,
    voucher_id bigint NOT NULL,
    source_type text NOT NULL,
    source_id bigint NOT NULL,
    content_sha256 text NOT NULL,
    stored_filename text NOT NULL,
    byte_size bigint NOT NULL,
    row_version bigint NOT NULL,
    captured_at text NOT NULL,
    CONSTRAINT voucher_attachment_links_byte_size_check CHECK ((byte_size >= 0)),
    CONSTRAINT voucher_attachment_links_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT voucher_attachment_links_row_version_check CHECK ((row_version >= 1))
);


--
-- Name: voucher_attachment_links_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.voucher_attachment_links ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.voucher_attachment_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: voucher_entries; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voucher_entries (
    id bigint NOT NULL,
    voucher_id bigint NOT NULL,
    line_no integer NOT NULL,
    account_id bigint NOT NULL,
    debit numeric(14,2) DEFAULT 0 NOT NULL,
    credit numeric(14,2) DEFAULT 0 NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    source_line_type text DEFAULT ''::text NOT NULL,
    source_line_id bigint,
    memo text DEFAULT ''::text NOT NULL,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT voucher_entries_check CHECK ((((debit > (0)::numeric) AND (credit = (0)::numeric)) OR ((credit > (0)::numeric) AND (debit = (0)::numeric)))),
    CONSTRAINT voucher_entries_credit_check CHECK ((credit >= (0)::numeric)),
    CONSTRAINT voucher_entries_currency_check CHECK ((currency = 'USD'::text)),
    CONSTRAINT voucher_entries_debit_check CHECK ((debit >= (0)::numeric)),
    CONSTRAINT voucher_entries_line_no_check CHECK ((line_no >= 1))
);


--
-- Name: voucher_entries_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.voucher_entries ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.voucher_entries_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: voucher_settlement_lines; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voucher_settlement_lines (
    id bigint NOT NULL,
    voucher_id bigint NOT NULL,
    voucher_entry_id bigint NOT NULL,
    target_type text NOT NULL,
    target_id bigint NOT NULL,
    amount numeric(14,2) NOT NULL,
    status text DEFAULT 'active'::text NOT NULL,
    reversed_by bigint,
    redirected_to bigint,
    currency text DEFAULT 'USD'::text NOT NULL,
    source_event_id bigint NOT NULL,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT voucher_settlement_lines_amount_check CHECK ((amount > (0)::numeric)),
    CONSTRAINT voucher_settlement_lines_check CHECK (((id IS DISTINCT FROM reversed_by) AND (id IS DISTINCT FROM redirected_to))),
    CONSTRAINT voucher_settlement_lines_currency_check CHECK ((currency = 'USD'::text)),
    CONSTRAINT voucher_settlement_lines_status_check CHECK ((status = ANY (ARRAY['active'::text, 'reversed'::text, 'redirected'::text]))),
    CONSTRAINT voucher_settlement_lines_target_type_check CHECK ((target_type = ANY (ARRAY['payable'::text, 'receivable'::text, 'prepayment'::text])))
);


--
-- Name: voucher_settlement_lines_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.voucher_settlement_lines ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.voucher_settlement_lines_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: voucher_source_links; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.voucher_source_links (
    id bigint NOT NULL,
    voucher_id bigint NOT NULL,
    source_type text NOT NULL,
    source_id bigint NOT NULL,
    source_version bigint NOT NULL,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    CONSTRAINT voucher_source_links_source_version_check CHECK ((source_version >= 1))
);


--
-- Name: voucher_source_links_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.voucher_source_links ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.voucher_source_links_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: vouchers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.vouchers (
    id bigint NOT NULL,
    voucher_number text NOT NULL,
    voucher_type text NOT NULL,
    status text DEFAULT 'draft'::text NOT NULL,
    business_date date NOT NULL,
    accounting_date date NOT NULL,
    currency text DEFAULT 'USD'::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    source_snapshot jsonb DEFAULT '{}'::jsonb NOT NULL,
    reversal_of bigint,
    corrects_opening_line bigint,
    reason_code text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text DEFAULT (CURRENT_TIMESTAMP)::text NOT NULL,
    posted_by bigint,
    posted_at text,
    reversed_by bigint,
    reversed_at text,
    CONSTRAINT vouchers_check CHECK ((id IS DISTINCT FROM reversal_of)),
    CONSTRAINT vouchers_currency_check CHECK ((currency = 'USD'::text)),
    CONSTRAINT vouchers_status_check CHECK ((status = ANY (ARRAY['draft'::text, 'posted'::text, 'reversed'::text])))
);


--
-- Name: vouchers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.vouchers ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.vouchers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: work_order_types; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.work_order_types (
    id bigint NOT NULL,
    code text NOT NULL,
    name text NOT NULL,
    description text,
    is_active bigint DEFAULT 1 NOT NULL,
    created_at text NOT NULL
);


--
-- Name: work_order_types_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.work_order_types ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.work_order_types_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: worker_tax_status_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.worker_tax_status_history (
    id bigint NOT NULL,
    employee_id bigint NOT NULL,
    tax_status text NOT NULL,
    effective_from text NOT NULL,
    effective_to text,
    notes text DEFAULT ''::text NOT NULL,
    created_by bigint,
    created_at text NOT NULL,
    CONSTRAINT worker_tax_status_history_check CHECK (((effective_to IS NULL) OR (effective_to >= effective_from))),
    CONSTRAINT worker_tax_status_history_tax_status_check CHECK ((tax_status = ANY (ARRAY['W2'::text, '1099'::text])))
);


--
-- Name: worker_tax_status_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.worker_tax_status_history ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.worker_tax_status_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: expense_ai_reviews id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_ai_reviews ALTER COLUMN id SET DEFAULT nextval('public.expense_ai_reviews_id_seq'::regclass);


--
-- Name: expense_attachment_interpretations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_attachment_interpretations ALTER COLUMN id SET DEFAULT nextval('public.expense_attachment_interpretations_id_seq'::regclass);


--
-- Name: expense_settlement_invoice_map id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_settlement_invoice_map ALTER COLUMN id SET DEFAULT nextval('public.expense_settlement_invoice_map_id_seq'::regclass);


--
-- Name: llm_configs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_configs ALTER COLUMN id SET DEFAULT nextval('public.llm_configs_id_seq'::regclass);


--
-- Name: accounting_base_check_report accounting_base_check_report_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_base_check_report
    ADD CONSTRAINT accounting_base_check_report_pkey PRIMARY KEY (id);


--
-- Name: accounting_opening_cutover accounting_opening_cutover_cutover_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_cutover
    ADD CONSTRAINT accounting_opening_cutover_cutover_date_key UNIQUE (cutover_date);


--
-- Name: accounting_opening_cutover accounting_opening_cutover_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_cutover
    ADD CONSTRAINT accounting_opening_cutover_pkey PRIMARY KEY (id);


--
-- Name: accounting_opening_lines accounting_opening_lines_origin_type_origin_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_lines
    ADD CONSTRAINT accounting_opening_lines_origin_type_origin_id_key UNIQUE (origin_type, origin_id);


--
-- Name: accounting_opening_lines accounting_opening_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_lines
    ADD CONSTRAINT accounting_opening_lines_pkey PRIMARY KEY (id);


--
-- Name: accounting_periods accounting_periods_period_start_period_end_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_periods
    ADD CONSTRAINT accounting_periods_period_start_period_end_key UNIQUE (period_start, period_end);


--
-- Name: accounting_periods accounting_periods_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_periods
    ADD CONSTRAINT accounting_periods_pkey PRIMARY KEY (id);


--
-- Name: accounts accounts_account_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_account_code_key UNIQUE (account_code);


--
-- Name: accounts accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_pkey PRIMARY KEY (id);


--
-- Name: ai_daily_report_actions ai_daily_report_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_actions
    ADD CONSTRAINT ai_daily_report_actions_pkey PRIMARY KEY (id);


--
-- Name: ai_daily_report_attachment_manifests ai_daily_report_attachment_manifests_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_attachment_manifests
    ADD CONSTRAINT ai_daily_report_attachment_manifests_pkey PRIMARY KEY (id);


--
-- Name: ai_daily_report_drafts ai_daily_report_drafts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_drafts
    ADD CONSTRAINT ai_daily_report_drafts_pkey PRIMARY KEY (id);


--
-- Name: ai_daily_report_formal_commits ai_daily_report_formal_commits_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_formal_commits
    ADD CONSTRAINT ai_daily_report_formal_commits_pkey PRIMARY KEY (id);


--
-- Name: ai_daily_report_manifest_roles ai_daily_report_manifest_roles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_manifest_roles
    ADD CONSTRAINT ai_daily_report_manifest_roles_pkey PRIMARY KEY (id);


--
-- Name: ai_daily_report_manifest_sources ai_daily_report_manifest_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_manifest_sources
    ADD CONSTRAINT ai_daily_report_manifest_sources_pkey PRIMARY KEY (id);


--
-- Name: ai_daily_report_prepared_assets ai_daily_report_prepared_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_prepared_assets
    ADD CONSTRAINT ai_daily_report_prepared_assets_pkey PRIMARY KEY (id);


--
-- Name: ai_photo_analysis ai_photo_analysis_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_photo_analysis
    ADD CONSTRAINT ai_photo_analysis_pkey PRIMARY KEY (id);


--
-- Name: asset_events asset_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_events
    ADD CONSTRAINT asset_events_pkey PRIMARY KEY (id);


--
-- Name: asset_photos asset_photos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_photos
    ADD CONSTRAINT asset_photos_pkey PRIMARY KEY (id);


--
-- Name: assets assets_asset_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_asset_number_key UNIQUE (asset_number);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: assets assets_stable_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_stable_id_key UNIQUE (stable_id);


--
-- Name: audit_logs audit_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_pkey PRIMARY KEY (id);


--
-- Name: bank_accounts bank_accounts_account_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_accounts
    ADD CONSTRAINT bank_accounts_account_name_key UNIQUE (account_name);


--
-- Name: bank_accounts bank_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_accounts
    ADD CONSTRAINT bank_accounts_pkey PRIMARY KEY (id);


--
-- Name: bank_transactions bank_transactions_import_fingerprint_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_import_fingerprint_key UNIQUE (import_fingerprint);


--
-- Name: bank_transactions bank_transactions_matched_batch_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_matched_batch_id_key UNIQUE (matched_batch_id);


--
-- Name: bank_transactions bank_transactions_matched_payment_order_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_matched_payment_order_id_key UNIQUE (matched_payment_order_id);


--
-- Name: bank_transactions bank_transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_pkey PRIMARY KEY (id);


--
-- Name: buyers buyers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.buyers
    ADD CONSTRAINT buyers_pkey PRIMARY KEY (id);


--
-- Name: clients clients_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clients
    ADD CONSTRAINT clients_pkey PRIMARY KEY (id);


--
-- Name: clock_in_photos clock_in_photos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clock_in_photos
    ADD CONSTRAINT clock_in_photos_pkey PRIMARY KEY (id);


--
-- Name: commercial_document_rates commercial_document_rates_commercial_document_id_rate_type_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commercial_document_rates
    ADD CONSTRAINT commercial_document_rates_commercial_document_id_rate_type_key UNIQUE (commercial_document_id, rate_type);


--
-- Name: commercial_document_rates commercial_document_rates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commercial_document_rates
    ADD CONSTRAINT commercial_document_rates_pkey PRIMARY KEY (id);


--
-- Name: commercial_documents commercial_documents_doc_type_doc_ref_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commercial_documents
    ADD CONSTRAINT commercial_documents_doc_type_doc_ref_id_key UNIQUE (doc_type, doc_ref_id);


--
-- Name: commercial_documents commercial_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commercial_documents
    ADD CONSTRAINT commercial_documents_pkey PRIMARY KEY (id);


--
-- Name: company_attachments company_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_attachments
    ADD CONSTRAINT company_attachments_pkey PRIMARY KEY (id);


--
-- Name: contract_attachments contract_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_attachments
    ADD CONSTRAINT contract_attachments_pkey PRIMARY KEY (id);


--
-- Name: contract_rate_items contract_rate_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_rate_items
    ADD CONSTRAINT contract_rate_items_pkey PRIMARY KEY (id);


--
-- Name: contract_rate_versions contract_rate_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_rate_versions
    ADD CONSTRAINT contract_rate_versions_pkey PRIMARY KEY (id);


--
-- Name: contracts contracts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_pkey PRIMARY KEY (id);


--
-- Name: countries countries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.countries
    ADD CONSTRAINT countries_pkey PRIMARY KEY (code);


--
-- Name: country_translations country_translations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.country_translations
    ADD CONSTRAINT country_translations_pkey PRIMARY KEY (country_code, language_code);


--
-- Name: customer_prepayment_applications customer_prepayment_applicati_prepayment_id_invoice_id_occu_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayment_applications
    ADD CONSTRAINT customer_prepayment_applicati_prepayment_id_invoice_id_occu_key UNIQUE (prepayment_id, invoice_id, occurrence_no);


--
-- Name: customer_prepayment_applications customer_prepayment_applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayment_applications
    ADD CONSTRAINT customer_prepayment_applications_pkey PRIMARY KEY (id);


--
-- Name: customer_prepayments customer_prepayments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayments
    ADD CONSTRAINT customer_prepayments_pkey PRIMARY KEY (id);


--
-- Name: customer_prepayments customer_prepayments_receipt_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayments
    ADD CONSTRAINT customer_prepayments_receipt_id_key UNIQUE (receipt_id);


--
-- Name: customer_receipts customer_receipts_customer_id_receipt_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_receipts
    ADD CONSTRAINT customer_receipts_customer_id_receipt_no_key UNIQUE (customer_id, receipt_no);


--
-- Name: customer_receipts customer_receipts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_receipts
    ADD CONSTRAINT customer_receipts_pkey PRIMARY KEY (id);


--
-- Name: customer_reimbursement_attachments customer_reimbursement_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_attachments
    ADD CONSTRAINT customer_reimbursement_attachments_pkey PRIMARY KEY (id);


--
-- Name: customer_reimbursement_expense_ignores customer_reimbursement_expense_ignores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_expense_ignores
    ADD CONSTRAINT customer_reimbursement_expense_ignores_pkey PRIMARY KEY (id);


--
-- Name: customer_reimbursement_expense_links customer_reimbursement_expense_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_expense_links
    ADD CONSTRAINT customer_reimbursement_expense_links_pkey PRIMARY KEY (id);


--
-- Name: customer_reimbursement_items customer_reimbursement_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_items
    ADD CONSTRAINT customer_reimbursement_items_pkey PRIMARY KEY (id);


--
-- Name: customer_reimbursements customer_reimbursements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursements
    ADD CONSTRAINT customer_reimbursements_pkey PRIMARY KEY (id);


--
-- Name: data_repair_archive data_repair_archive_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.data_repair_archive
    ADD CONSTRAINT data_repair_archive_pkey PRIMARY KEY (id);


--
-- Name: email_delivery_logs email_delivery_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_delivery_logs
    ADD CONSTRAINT email_delivery_logs_pkey PRIMARY KEY (id);


--
-- Name: employee_advance_applications employee_advance_applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advance_applications
    ADD CONSTRAINT employee_advance_applications_pkey PRIMARY KEY (id);


--
-- Name: employee_advances employee_advances_advance_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advances
    ADD CONSTRAINT employee_advances_advance_number_key UNIQUE (advance_number);


--
-- Name: employee_advances employee_advances_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advances
    ADD CONSTRAINT employee_advances_pkey PRIMARY KEY (id);


--
-- Name: employee_grades employee_grades_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_grades
    ADD CONSTRAINT employee_grades_pkey PRIMARY KEY (id);


--
-- Name: employee_payment_batches employee_payment_batches_batch_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_batches
    ADD CONSTRAINT employee_payment_batches_batch_number_key UNIQUE (batch_number);


--
-- Name: employee_payment_batches employee_payment_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_batches
    ADD CONSTRAINT employee_payment_batches_pkey PRIMARY KEY (id);


--
-- Name: employee_payment_components employee_payment_components_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_components
    ADD CONSTRAINT employee_payment_components_pkey PRIMARY KEY (id);


--
-- Name: employee_payment_orders employee_payment_orders_payment_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_payment_number_key UNIQUE (payment_number);


--
-- Name: employee_payment_orders employee_payment_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_pkey PRIMARY KEY (id);


--
-- Name: employee_payment_orders employee_payment_orders_source_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_source_key_key UNIQUE (source_key);


--
-- Name: employee_payment_tax_reviews employee_payment_tax_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_tax_reviews
    ADD CONSTRAINT employee_payment_tax_reviews_pkey PRIMARY KEY (id);


--
-- Name: employee_rate_items employee_rate_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_rate_items
    ADD CONSTRAINT employee_rate_items_pkey PRIMARY KEY (id);


--
-- Name: employee_rate_versions employee_rate_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_rate_versions
    ADD CONSTRAINT employee_rate_versions_pkey PRIMARY KEY (id);


--
-- Name: employee_salary_agreements employee_salary_agreements_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_salary_agreements
    ADD CONSTRAINT employee_salary_agreements_pkey PRIMARY KEY (id);


--
-- Name: expense_ai_reviews expense_ai_reviews_expense_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_ai_reviews
    ADD CONSTRAINT expense_ai_reviews_expense_id_key UNIQUE (expense_id);


--
-- Name: expense_ai_reviews expense_ai_reviews_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_ai_reviews
    ADD CONSTRAINT expense_ai_reviews_pkey PRIMARY KEY (id);


--
-- Name: expense_attachment_interpretations expense_attachment_interpretations_attachment_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_attachment_interpretations
    ADD CONSTRAINT expense_attachment_interpretations_attachment_id_key UNIQUE (attachment_id);


--
-- Name: expense_attachment_interpretations expense_attachment_interpretations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_attachment_interpretations
    ADD CONSTRAINT expense_attachment_interpretations_pkey PRIMARY KEY (id);


--
-- Name: expense_attachments expense_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_attachments
    ADD CONSTRAINT expense_attachments_pkey PRIMARY KEY (id);


--
-- Name: expense_duplicate_checks expense_duplicate_checks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_duplicate_checks
    ADD CONSTRAINT expense_duplicate_checks_pkey PRIMARY KEY (id);


--
-- Name: expense_items expense_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_items
    ADD CONSTRAINT expense_items_pkey PRIMARY KEY (id);


--
-- Name: expense_save_tokens expense_save_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_save_tokens
    ADD CONSTRAINT expense_save_tokens_pkey PRIMARY KEY (token);


--
-- Name: expense_settlement_invoice_map expense_settlement_invoice_map_expense_project_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_settlement_invoice_map
    ADD CONSTRAINT expense_settlement_invoice_map_expense_project_name_key UNIQUE (expense_project_name);


--
-- Name: expense_settlement_invoice_map expense_settlement_invoice_map_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_settlement_invoice_map
    ADD CONSTRAINT expense_settlement_invoice_map_pkey PRIMARY KEY (id);


--
-- Name: expenses expenses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_pkey PRIMARY KEY (id);


--
-- Name: field_photos field_photos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_photos
    ADD CONSTRAINT field_photos_pkey PRIMARY KEY (id);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_invoice_id_correction_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_invoice_id_correction_no_key UNIQUE (invoice_id, correction_no);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_pkey PRIMARY KEY (id);


--
-- Name: invoice_attachments invoice_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_attachments
    ADD CONSTRAINT invoice_attachments_pkey PRIMARY KEY (id);


--
-- Name: invoice_items invoice_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_pkey PRIMARY KEY (id);


--
-- Name: invoice_save_tokens invoice_save_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_save_tokens
    ADD CONSTRAINT invoice_save_tokens_pkey PRIMARY KEY (token);


--
-- Name: invoice_schema_version invoice_schema_version_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_schema_version
    ADD CONSTRAINT invoice_schema_version_pkey PRIMARY KEY (singleton);


--
-- Name: invoice_sqlite_columns invoice_sqlite_columns_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_sqlite_columns
    ADD CONSTRAINT invoice_sqlite_columns_pkey PRIMARY KEY (table_name, cid);


--
-- Name: invoices invoices_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_pkey PRIMARY KEY (id);


--
-- Name: knowledge_document_versions knowledge_document_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_document_versions
    ADD CONSTRAINT knowledge_document_versions_pkey PRIMARY KEY (id);


--
-- Name: knowledge_documents knowledge_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_documents
    ADD CONSTRAINT knowledge_documents_pkey PRIMARY KEY (id);


--
-- Name: llm_configs llm_configs_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_configs
    ADD CONSTRAINT llm_configs_name_key UNIQUE (name);


--
-- Name: llm_configs llm_configs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.llm_configs
    ADD CONSTRAINT llm_configs_pkey PRIMARY KEY (id);


--
-- Name: manufacturers manufacturers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.manufacturers
    ADD CONSTRAINT manufacturers_pkey PRIMARY KEY (id);


--
-- Name: messages messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_pkey PRIMARY KEY (id);


--
-- Name: owners owners_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.owners
    ADD CONSTRAINT owners_pkey PRIMARY KEY (id);


--
-- Name: payment_order_events payment_order_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_order_events
    ADD CONSTRAINT payment_order_events_pkey PRIMARY KEY (id);


--
-- Name: payment_order_sources payment_order_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_order_sources
    ADD CONSTRAINT payment_order_sources_pkey PRIMARY KEY (id);


--
-- Name: payment_terms payment_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_terms
    ADD CONSTRAINT payment_terms_pkey PRIMARY KEY (id);


--
-- Name: payroll_component_correction_allocations payroll_component_correction_allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_correction_allocations
    ADD CONSTRAINT payroll_component_correction_allocations_pkey PRIMARY KEY (id);


--
-- Name: payroll_component_tax_config payroll_component_tax_config_component_code_effective_from_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_tax_config
    ADD CONSTRAINT payroll_component_tax_config_component_code_effective_from_key UNIQUE (component_code, effective_from);


--
-- Name: payroll_component_tax_config payroll_component_tax_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_tax_config
    ADD CONSTRAINT payroll_component_tax_config_pkey PRIMARY KEY (id);


--
-- Name: payroll_correction_groups payroll_correction_groups_correction_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_correction_groups
    ADD CONSTRAINT payroll_correction_groups_correction_code_key UNIQUE (correction_code);


--
-- Name: payroll_correction_groups payroll_correction_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_correction_groups
    ADD CONSTRAINT payroll_correction_groups_pkey PRIMARY KEY (id);


--
-- Name: posting_audit posting_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posting_audit
    ADD CONSTRAINT posting_audit_pkey PRIMARY KEY (id);


--
-- Name: posting_events posting_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posting_events
    ADD CONSTRAINT posting_events_pkey PRIMARY KEY (id);


--
-- Name: profit_ledger profit_ledger_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profit_ledger
    ADD CONSTRAINT profit_ledger_pkey PRIMARY KEY (id);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: quotation_revisions quotation_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotation_revisions
    ADD CONSTRAINT quotation_revisions_pkey PRIMARY KEY (id);


--
-- Name: quotation_revisions quotation_revisions_quotation_id_revision_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotation_revisions
    ADD CONSTRAINT quotation_revisions_quotation_id_revision_no_key UNIQUE (quotation_id, revision_no);


--
-- Name: quotations quotations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotations
    ADD CONSTRAINT quotations_pkey PRIMARY KEY (id);


--
-- Name: quotations quotations_quotation_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotations
    ADD CONSTRAINT quotations_quotation_number_key UNIQUE (quotation_number);


--
-- Name: receipt_allocations receipt_allocations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipt_allocations
    ADD CONSTRAINT receipt_allocations_pkey PRIMARY KEY (id);


--
-- Name: role_action_permissions role_action_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_action_permissions
    ADD CONSTRAINT role_action_permissions_pkey PRIMARY KEY (role, resource_key, action_key);


--
-- Name: role_menu_permissions role_menu_permissions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_menu_permissions
    ADD CONSTRAINT role_menu_permissions_pkey PRIMARY KEY (role, menu_key);


--
-- Name: service_orders service_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_pkey PRIMARY KEY (id);


--
-- Name: service_orders service_orders_settlement_basis_check; Type: CHECK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE public.service_orders
    ADD CONSTRAINT service_orders_settlement_basis_check CHECK ((settlement_basis = ANY (ARRAY['contract_actual'::text, 'quotation'::text]))) NOT VALID;


--
-- Name: service_report_attachments service_report_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_attachments
    ADD CONSTRAINT service_report_attachments_pkey PRIMARY KEY (id);


--
-- Name: service_report_mileage_evidence service_report_mileage_evidence_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_mileage_evidence
    ADD CONSTRAINT service_report_mileage_evidence_pkey PRIMARY KEY (id);


--
-- Name: service_report_mileage_evidence service_report_mileage_evidence_report_id_worker_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_mileage_evidence
    ADD CONSTRAINT service_report_mileage_evidence_report_id_worker_user_id_key UNIQUE (report_id, worker_user_id);


--
-- Name: service_report_replaced_parts service_report_replaced_parts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_replaced_parts
    ADD CONSTRAINT service_report_replaced_parts_pkey PRIMARY KEY (id);


--
-- Name: service_report_save_tokens service_report_save_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_save_tokens
    ADD CONSTRAINT service_report_save_tokens_pkey PRIMARY KEY (token);


--
-- Name: service_report_saved_parts service_report_saved_parts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_saved_parts
    ADD CONSTRAINT service_report_saved_parts_pkey PRIMARY KEY (id);


--
-- Name: service_report_workers service_report_workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_workers
    ADD CONSTRAINT service_report_workers_pkey PRIMARY KEY (id);


--
-- Name: service_reports service_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_reports
    ADD CONSTRAINT service_reports_pkey PRIMARY KEY (id);


--
-- Name: settings settings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.settings
    ADD CONSTRAINT settings_pkey PRIMARY KEY (key);


--
-- Name: payroll_component_correction_allocations uq_correction_allocation_component; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_correction_allocations
    ADD CONSTRAINT uq_correction_allocation_component UNIQUE (correction_group_id, component_id);


--
-- Name: user_attachments user_attachments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_attachments
    ADD CONSTRAINT user_attachments_pkey PRIMARY KEY (id);


--
-- Name: user_service_orders user_service_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_service_orders
    ADD CONSTRAINT user_service_orders_pkey PRIMARY KEY (user_id, service_order_id);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: voucher_attachment_links voucher_attachment_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_attachment_links
    ADD CONSTRAINT voucher_attachment_links_pkey PRIMARY KEY (id);


--
-- Name: voucher_attachment_links voucher_attachment_links_voucher_id_source_type_source_id_r_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_attachment_links
    ADD CONSTRAINT voucher_attachment_links_voucher_id_source_type_source_id_r_key UNIQUE (voucher_id, source_type, source_id, row_version);


--
-- Name: voucher_entries voucher_entries_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_entries
    ADD CONSTRAINT voucher_entries_pkey PRIMARY KEY (id);


--
-- Name: voucher_entries voucher_entries_voucher_id_line_no_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_entries
    ADD CONSTRAINT voucher_entries_voucher_id_line_no_key UNIQUE (voucher_id, line_no);


--
-- Name: voucher_settlement_lines voucher_settlement_lines_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_settlement_lines
    ADD CONSTRAINT voucher_settlement_lines_pkey PRIMARY KEY (id);


--
-- Name: voucher_settlement_lines voucher_settlement_lines_voucher_entry_id_source_event_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_settlement_lines
    ADD CONSTRAINT voucher_settlement_lines_voucher_entry_id_source_event_id_key UNIQUE (voucher_entry_id, source_event_id);


--
-- Name: voucher_source_links voucher_source_links_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_source_links
    ADD CONSTRAINT voucher_source_links_pkey PRIMARY KEY (id);


--
-- Name: voucher_source_links voucher_source_links_voucher_id_source_type_source_id_sourc_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_source_links
    ADD CONSTRAINT voucher_source_links_voucher_id_source_type_source_id_sourc_key UNIQUE (voucher_id, source_type, source_id, source_version);


--
-- Name: vouchers vouchers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_pkey PRIMARY KEY (id);


--
-- Name: vouchers vouchers_voucher_number_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_voucher_number_key UNIQUE (voucher_number);


--
-- Name: work_order_types work_order_types_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.work_order_types
    ADD CONSTRAINT work_order_types_pkey PRIMARY KEY (id);


--
-- Name: worker_tax_status_history worker_tax_status_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_tax_status_history
    ADD CONSTRAINT worker_tax_status_history_pkey PRIMARY KEY (id);


--
-- Name: idx_advance_applications_advance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_advance_applications_advance ON public.employee_advance_applications USING btree (advance_id, id);


--
-- Name: idx_advance_applications_payment; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_advance_applications_payment ON public.employee_advance_applications USING btree (payment_order_id, id);


--
-- Name: idx_ai_actions_draft; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_actions_draft ON public.ai_daily_report_actions USING btree (draft_id);


--
-- Name: idx_ai_draft_order_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_draft_order_date ON public.ai_daily_report_drafts USING btree (service_order_id, report_date);


--
-- Name: idx_ai_formal_commit_draft; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_formal_commit_draft ON public.ai_daily_report_formal_commits USING btree (draft_id, status);


--
-- Name: idx_ai_formal_commit_report; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_formal_commit_report ON public.ai_daily_report_formal_commits USING btree (service_report_id);


--
-- Name: idx_ai_manifest_asset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_manifest_asset ON public.ai_daily_report_prepared_assets USING btree (manifest_id);


--
-- Name: idx_ai_manifest_draft; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_manifest_draft ON public.ai_daily_report_attachment_manifests USING btree (draft_id, status);


--
-- Name: idx_ai_manifest_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ai_manifest_source ON public.ai_daily_report_manifest_sources USING btree (manifest_id);


--
-- Name: idx_asset_events_asset; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_asset_events_asset ON public.asset_events USING btree (asset_id, id);


--
-- Name: idx_assets_holder; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_holder ON public.assets USING btree (current_holder_id, status);


--
-- Name: idx_assets_service_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assets_service_order ON public.assets USING btree (service_order_id);


--
-- Name: idx_bank_transactions_account_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bank_transactions_account_date ON public.bank_transactions USING btree (bank_account_id, transaction_date);


--
-- Name: idx_bank_transactions_unmatched; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bank_transactions_unmatched ON public.bank_transactions USING btree (matched_payment_order_id) WHERE (matched_payment_order_id IS NULL);


--
-- Name: idx_commercial_document_rates_doc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commercial_document_rates_doc ON public.commercial_document_rates USING btree (commercial_document_id);


--
-- Name: idx_commercial_documents_client; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commercial_documents_client ON public.commercial_documents USING btree (client_id);


--
-- Name: idx_commercial_documents_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_commercial_documents_type ON public.commercial_documents USING btree (doc_type);


--
-- Name: idx_contract_rate_versions_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_contract_rate_versions_lookup ON public.contract_rate_versions USING btree (contract_id, status, effective_from, effective_to);


--
-- Name: idx_corr_alloc_canonical_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_corr_alloc_canonical_order ON public.payroll_component_correction_allocations USING btree (canonical_payment_order_id);


--
-- Name: idx_corr_alloc_component; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_corr_alloc_component ON public.payroll_component_correction_allocations USING btree (component_id);


--
-- Name: idx_corr_alloc_disposition; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_corr_alloc_disposition ON public.payroll_component_correction_allocations USING btree (disposition);


--
-- Name: idx_corr_alloc_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_corr_alloc_group ON public.payroll_component_correction_allocations USING btree (correction_group_id, disposition);


--
-- Name: idx_customer_reimbursement_attachment_expense_source; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_customer_reimbursement_attachment_expense_source ON public.customer_reimbursement_attachments USING btree (source_expense_attachment_id) WHERE (source_expense_attachment_id IS NOT NULL);


--
-- Name: idx_customer_reimbursement_expense_ignores_expense; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_reimbursement_expense_ignores_expense ON public.customer_reimbursement_expense_ignores USING btree (expense_item_id);


--
-- Name: idx_customer_reimbursement_expense_links_expense; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_reimbursement_expense_links_expense ON public.customer_reimbursement_expense_links USING btree (expense_item_id);


--
-- Name: idx_customer_reimbursement_expense_links_reimbursement; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_reimbursement_expense_links_reimbursement ON public.customer_reimbursement_expense_links USING btree (customer_reimbursement_id, expense_item_id);


--
-- Name: idx_customer_reimbursements_quote_revision; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_customer_reimbursements_quote_revision ON public.customer_reimbursements USING btree (quotation_revision_id);


--
-- Name: idx_email_delivery_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_email_delivery_entity ON public.email_delivery_logs USING btree (entity_type, entity_id, sent_at);


--
-- Name: idx_employee_advances_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_employee_advances_employee ON public.employee_advances USING btree (employee_id, status);


--
-- Name: idx_employee_payments_batch; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_employee_payments_batch ON public.employee_payment_orders USING btree (batch_id);


--
-- Name: idx_employee_payments_correction_group; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_employee_payments_correction_group ON public.employee_payment_orders USING btree (correction_group_id);


--
-- Name: idx_employee_payments_correction_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_employee_payments_correction_status ON public.employee_payment_orders USING btree (correction_status);


--
-- Name: idx_employee_payments_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_employee_payments_employee ON public.employee_payment_orders USING btree (employee_id, status);


--
-- Name: idx_employee_payments_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_employee_payments_status ON public.employee_payment_orders USING btree (status, created_at);


--
-- Name: idx_employee_rate_versions_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_employee_rate_versions_lookup ON public.employee_rate_versions USING btree (employee_grade_id, status, effective_from, effective_to);


--
-- Name: idx_expense_ai_reviews_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_expense_ai_reviews_status ON public.expense_ai_reviews USING btree (status);


--
-- Name: idx_expense_attachments_sha256; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_expense_attachments_sha256 ON public.expense_attachments USING btree (file_sha256);


--
-- Name: idx_expense_duplicate_checks_expense; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_expense_duplicate_checks_expense ON public.expense_duplicate_checks USING btree (expense_id);


--
-- Name: idx_expense_interpretations_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_expense_interpretations_status ON public.expense_attachment_interpretations USING btree (status);


--
-- Name: idx_expenses_beneficiary; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_expenses_beneficiary ON public.expenses USING btree (beneficiary_id);


--
-- Name: idx_field_photos_order_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_field_photos_order_date ON public.field_photos USING btree (order_id, capture_date);


--
-- Name: idx_field_photos_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_field_photos_user ON public.field_photos USING btree (user_id);


--
-- Name: idx_invoice_sqlite_columns_table_name; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_invoice_sqlite_columns_table_name ON public.invoice_sqlite_columns USING btree (table_name, name);


--
-- Name: idx_payment_batches_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_batches_employee ON public.employee_payment_batches USING btree (employee_id, status);


--
-- Name: idx_payment_batches_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_batches_status ON public.employee_payment_batches USING btree (status, issued_at);


--
-- Name: idx_payment_components_employee_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_components_employee_date ON public.employee_payment_components USING btree (employee_id, service_date);


--
-- Name: idx_payment_components_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_components_order ON public.employee_payment_components USING btree (payment_order_id);


--
-- Name: idx_payment_components_review; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_components_review ON public.employee_payment_components USING btree (review_status);


--
-- Name: idx_payment_components_tax_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_components_tax_category ON public.employee_payment_components USING btree (tax_category);


--
-- Name: idx_payment_components_tax_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_components_tax_status ON public.employee_payment_components USING btree (tax_status_snapshot);


--
-- Name: idx_payment_events_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_events_order ON public.payment_order_events USING btree (payment_order_id, id);


--
-- Name: idx_payment_tax_reviews_component; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payment_tax_reviews_component ON public.employee_payment_tax_reviews USING btree (component_id);


--
-- Name: idx_payroll_component_tax_config_lookup; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payroll_component_tax_config_lookup ON public.payroll_component_tax_config USING btree (component_code, effective_from);


--
-- Name: idx_payroll_correction_groups_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_payroll_correction_groups_status ON public.payroll_correction_groups USING btree (status, correction_code);


--
-- Name: idx_profit_ledger_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_profit_ledger_date ON public.profit_ledger USING btree (work_date);


--
-- Name: idx_profit_ledger_order; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_profit_ledger_order ON public.profit_ledger USING btree (service_order_id, work_date);


--
-- Name: idx_quotation_revisions_quote; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quotation_revisions_quote ON public.quotation_revisions USING btree (quotation_id, revision_no DESC);


--
-- Name: idx_quotations_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quotations_created_at ON public.quotations USING btree (created_at);


--
-- Name: idx_quotations_number; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quotations_number ON public.quotations USING btree (quotation_number);


--
-- Name: idx_quotations_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_quotations_status ON public.quotations USING btree (status);


--
-- Name: idx_salary_agreements_employee_dates; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_salary_agreements_employee_dates ON public.employee_salary_agreements USING btree (employee_id, effective_from, effective_to);


--
-- Name: idx_service_orders_quotation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_service_orders_quotation_id ON public.service_orders USING btree (quotation_id);


--
-- Name: idx_sr_mileage_evidence_report; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sr_mileage_evidence_report ON public.service_report_mileage_evidence USING btree (report_id);


--
-- Name: idx_worker_tax_status_employee; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_worker_tax_status_employee ON public.worker_tax_status_history USING btree (employee_id, effective_from);


--
-- Name: ix_invoice_accounting_corrections_invoice; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_invoice_accounting_corrections_invoice ON public.invoice_accounting_corrections USING btree (invoice_id, status);


--
-- Name: ix_prepayment_applications_invoice; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_prepayment_applications_invoice ON public.customer_prepayment_applications USING btree (invoice_id, status);


--
-- Name: uq_0d922c678b23e416890c; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_0d922c678b23e416890c ON public.ai_daily_report_formal_commits USING btree (draft_id);


--
-- Name: uq_0e28e341315ea6760352; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_0e28e341315ea6760352 ON public.manufacturers USING btree (manufacturer_number);


--
-- Name: uq_0f63a05a71c4d9f523b9; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_0f63a05a71c4d9f523b9 ON public.clock_in_photos USING btree (relative_path);


--
-- Name: uq_10ed216eb0b35308dbd7; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_10ed216eb0b35308dbd7 ON public.field_photos USING btree (relative_path);


--
-- Name: uq_1787c777b0288ef3c708; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_1787c777b0288ef3c708 ON public.payment_terms USING btree (name);


--
-- Name: uq_18cb413f922760433c4e; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_18cb413f922760433c4e ON public.field_photos USING btree (user_id, client_id);


--
-- Name: uq_2350e5b3a8c20030cc8c; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_2350e5b3a8c20030cc8c ON public.ai_daily_report_prepared_assets USING btree (asset_id);


--
-- Name: uq_2ac5f5e622511b42e1f9; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_2ac5f5e622511b42e1f9 ON public.contract_rate_items USING btree (version_id, rate_type);


--
-- Name: uq_34cf0faf1411b56d7b72; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_34cf0faf1411b56d7b72 ON public.employee_rate_items USING btree (version_id, rate_type);


--
-- Name: uq_47ec1accc8c78a329e83; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_47ec1accc8c78a329e83 ON public.users USING btree (email);


--
-- Name: uq_527fa53cfe8516b90fe1; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_527fa53cfe8516b90fe1 ON public.work_order_types USING btree (code);


--
-- Name: uq_5567e2d32f8f072bb0b7; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_5567e2d32f8f072bb0b7 ON public.ai_daily_report_manifest_roles USING btree (manifest_id, role_type, source_id);


--
-- Name: uq_5a82e4aae42a7bdd434e; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_5a82e4aae42a7bdd434e ON public.buyers USING btree (buyer_number);


--
-- Name: uq_627c89fc2360f386300d; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_627c89fc2360f386300d ON public.employee_rate_versions USING btree (employee_grade_id, version_no);


--
-- Name: uq_68efe1a3e4f32ac1298c; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_68efe1a3e4f32ac1298c ON public.ai_daily_report_prepared_assets USING btree (manifest_id, prepared_sha256);


--
-- Name: uq_6a21b0f2bb06db0a2345; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_6a21b0f2bb06db0a2345 ON public.contract_rate_versions USING btree (contract_id, version_no);


--
-- Name: uq_7efd4dc1d52f3923e528; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_7efd4dc1d52f3923e528 ON public.ai_photo_analysis USING btree (photo_path, photo_hash, analysis_model, analysis_version);


--
-- Name: uq_7f8e8d2c36d62d98f609; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_7f8e8d2c36d62d98f609 ON public.email_delivery_logs USING btree (source_audit_log_id);


--
-- Name: uq_85dc54f59aff54afffca; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_85dc54f59aff54afffca ON public.expense_duplicate_checks USING btree (expense_id, attachment_id, matched_attachment_id);


--
-- Name: uq_880afc9f5cf86d8c6d75; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_880afc9f5cf86d8c6d75 ON public.knowledge_document_versions USING btree (document_id, version_number);


--
-- Name: uq_910db4cdce940acb218d; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_910db4cdce940acb218d ON public.ai_daily_report_attachment_manifests USING btree (manifest_id);


--
-- Name: uq_9380b542dc5aa7864e97; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_9380b542dc5aa7864e97 ON public.data_repair_archive USING btree (repair_key, table_name, source_rowid);


--
-- Name: uq_9aa2dbf1ae0e992e45fb; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_9aa2dbf1ae0e992e45fb ON public.service_orders USING btree (order_number);


--
-- Name: uq_9e71b441ce74bb77cdbf; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_9e71b441ce74bb77cdbf ON public.employee_grades USING btree (grade_name);


--
-- Name: uq_9f319d5fa6e43bab02e2; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_9f319d5fa6e43bab02e2 ON public.knowledge_document_versions USING btree (stored_filename);


--
-- Name: uq_active_receipt_invoice_allocation; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_active_receipt_invoice_allocation ON public.receipt_allocations USING btree (receipt_id, invoice_id) WHERE (status = 'active'::text);


--
-- Name: uq_active_voucher_settlement_target_event; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_active_voucher_settlement_target_event ON public.voucher_settlement_lines USING btree (target_type, target_id, source_event_id) WHERE (status = 'active'::text);


--
-- Name: uq_advance_application_occurrence; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_advance_application_occurrence ON public.employee_advance_applications USING btree (advance_id, payment_order_id, occurrence_no) WHERE (occurrence_no IS NOT NULL);


--
-- Name: uq_af526c6836bd29281cf9; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_af526c6836bd29281cf9 ON public.clients USING btree (client_number);


--
-- Name: uq_bce6fc803fff79a975e6; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_bce6fc803fff79a975e6 ON public.expenses USING btree (expense_number);


--
-- Name: uq_be219b2c99e61e94a2cc; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_be219b2c99e61e94a2cc ON public.ai_daily_report_manifest_sources USING btree (source_id);


--
-- Name: uq_customer_reimbursement_expense_ignores; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_customer_reimbursement_expense_ignores ON public.customer_reimbursement_expense_ignores USING btree (customer_reimbursement_id, expense_item_id);


--
-- Name: uq_d1d0b625537dea8c21d8; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_d1d0b625537dea8c21d8 ON public.contracts USING btree (contract_number);


--
-- Name: uq_d7eab193675eec963dfa; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_d7eab193675eec963dfa ON public.ai_daily_report_formal_commits USING btree (commit_id);


--
-- Name: uq_dfb33784a594e6eca9f8; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_dfb33784a594e6eca9f8 ON public.ai_daily_report_attachment_manifests USING btree (draft_id, draft_version, validation_fingerprint, manifest_fingerprint);


--
-- Name: uq_e7787fc98ddc919bd6d4; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_e7787fc98ddc919bd6d4 ON public.ai_daily_report_formal_commits USING btree (draft_id, manifest_fingerprint);


--
-- Name: uq_e90c927f76a205a53dc4; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_e90c927f76a205a53dc4 ON public.customer_reimbursement_expense_links USING btree (customer_reimbursement_id, expense_item_id);


--
-- Name: uq_ec887ab86975384f1fbd; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_ec887ab86975384f1fbd ON public.ai_daily_report_actions USING btree (draft_id, action_id);


--
-- Name: uq_f08f842e87de46a90eb1; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_f08f842e87de46a90eb1 ON public.owners USING btree (owner_number);


--
-- Name: uq_f31235628388375a6110; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_f31235628388375a6110 ON public.knowledge_documents USING btree (stored_filename);


--
-- Name: uq_fbde7bff2f60b84f54df; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_fbde7bff2f60b84f54df ON public.invoices USING btree (invoice_number);


--
-- Name: uq_fd298cf8a507528f9664; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_fd298cf8a507528f9664 ON public.ai_daily_report_manifest_sources USING btree (manifest_id, source_identity);


--
-- Name: uq_posting_events_business_event; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_posting_events_business_event ON public.posting_events USING btree (source_type, source_id, related_source_id, source_version, event_type, occurrence_no);


--
-- Name: uq_posting_events_idempotency_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_posting_events_idempotency_key ON public.posting_events USING btree (idempotency_key) WHERE (idempotency_key IS NOT NULL);


--
-- Name: ux_service_orders_one_quotation; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ux_service_orders_one_quotation ON public.service_orders USING btree (commercial_document_id) WHERE ((commercial_document_id IS NOT NULL) AND (commercial_document_type = 'quotation'::text));


--
-- Name: accounting_opening_cutover accounting_opening_cutover_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER accounting_opening_cutover_guard BEFORE DELETE OR UPDATE ON public.accounting_opening_cutover FOR EACH ROW EXECUTE FUNCTION public.protect_posted_accounting_opening_cutover();


--
-- Name: accounting_opening_lines accounting_opening_lines_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER accounting_opening_lines_guard BEFORE INSERT OR DELETE OR UPDATE ON public.accounting_opening_lines FOR EACH ROW EXECUTE FUNCTION public.protect_accounting_opening_line();


--
-- Name: accounting_periods accounting_periods_integrity_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER accounting_periods_integrity_guard BEFORE INSERT OR UPDATE OF period_start, period_end ON public.accounting_periods FOR EACH ROW EXECUTE FUNCTION public.guard_accounting_period();


--
-- Name: invoice_items invoice_items_posted_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER invoice_items_posted_guard BEFORE INSERT OR DELETE OR UPDATE ON public.invoice_items FOR EACH ROW EXECUTE FUNCTION public.guard_posted_invoice_item();


--
-- Name: invoices invoices_posted_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER invoices_posted_guard BEFORE DELETE OR UPDATE ON public.invoices FOR EACH ROW EXECUTE FUNCTION public.guard_posted_invoice();


--
-- Name: voucher_attachment_links voucher_attachment_links_protect_posted; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER voucher_attachment_links_protect_posted BEFORE INSERT OR DELETE OR UPDATE ON public.voucher_attachment_links FOR EACH ROW EXECUTE FUNCTION public.protect_posted_voucher_child();


--
-- Name: voucher_entries voucher_entries_balance_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER voucher_entries_balance_check AFTER INSERT OR DELETE OR UPDATE ON public.voucher_entries DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_voucher_balance();


--
-- Name: voucher_entries voucher_entries_protect_posted; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER voucher_entries_protect_posted BEFORE INSERT OR DELETE OR UPDATE ON public.voucher_entries FOR EACH ROW EXECUTE FUNCTION public.protect_posted_voucher_child();


--
-- Name: voucher_settlement_lines voucher_settlement_lines_protect_posted; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER voucher_settlement_lines_protect_posted BEFORE INSERT OR DELETE OR UPDATE ON public.voucher_settlement_lines FOR EACH ROW EXECUTE FUNCTION public.protect_posted_voucher_child();


--
-- Name: voucher_source_links voucher_source_links_protect_posted; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER voucher_source_links_protect_posted BEFORE INSERT OR DELETE OR UPDATE ON public.voucher_source_links FOR EACH ROW EXECUTE FUNCTION public.protect_posted_voucher_child();


--
-- Name: vouchers vouchers_balance_check; Type: TRIGGER; Schema: public; Owner: -
--

CREATE CONSTRAINT TRIGGER vouchers_balance_check AFTER INSERT OR UPDATE OF status ON public.vouchers DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_voucher_balance();


--
-- Name: vouchers vouchers_mutation_guard; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER vouchers_mutation_guard BEFORE DELETE OR UPDATE ON public.vouchers FOR EACH ROW EXECUTE FUNCTION public.guard_voucher_mutation();


--
-- Name: accounting_base_check_report accounting_base_check_report_saved_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_base_check_report
    ADD CONSTRAINT accounting_base_check_report_saved_by_fkey FOREIGN KEY (saved_by) REFERENCES public.users(id);


--
-- Name: accounting_opening_cutover accounting_opening_cutover_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_cutover
    ADD CONSTRAINT accounting_opening_cutover_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: accounting_opening_cutover accounting_opening_cutover_posted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_cutover
    ADD CONSTRAINT accounting_opening_cutover_posted_by_fkey FOREIGN KEY (posted_by) REFERENCES public.users(id);


--
-- Name: accounting_opening_cutover accounting_opening_cutover_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_cutover
    ADD CONSTRAINT accounting_opening_cutover_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: accounting_opening_lines accounting_opening_lines_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_lines
    ADD CONSTRAINT accounting_opening_lines_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- Name: accounting_opening_lines accounting_opening_lines_cutover_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_lines
    ADD CONSTRAINT accounting_opening_lines_cutover_id_fkey FOREIGN KEY (cutover_id) REFERENCES public.accounting_opening_cutover(id);


--
-- Name: accounting_opening_lines accounting_opening_lines_voucher_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_opening_lines
    ADD CONSTRAINT accounting_opening_lines_voucher_entry_id_fkey FOREIGN KEY (voucher_entry_id) REFERENCES public.voucher_entries(id);


--
-- Name: accounting_periods accounting_periods_closed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_periods
    ADD CONSTRAINT accounting_periods_closed_by_fkey FOREIGN KEY (closed_by) REFERENCES public.users(id);


--
-- Name: accounting_periods accounting_periods_reopened_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounting_periods
    ADD CONSTRAINT accounting_periods_reopened_by_fkey FOREIGN KEY (reopened_by) REFERENCES public.users(id);


--
-- Name: ai_daily_report_actions ai_daily_report_actions_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_actions
    ADD CONSTRAINT ai_daily_report_actions_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.ai_daily_report_drafts(id) ON DELETE CASCADE;


--
-- Name: ai_daily_report_attachment_manifests ai_daily_report_attachment_manifests_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_attachment_manifests
    ADD CONSTRAINT ai_daily_report_attachment_manifests_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.ai_daily_report_drafts(id) ON DELETE CASCADE;


--
-- Name: ai_daily_report_drafts ai_daily_report_drafts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_drafts
    ADD CONSTRAINT ai_daily_report_drafts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: ai_daily_report_drafts ai_daily_report_drafts_service_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_drafts
    ADD CONSTRAINT ai_daily_report_drafts_service_order_id_fkey FOREIGN KEY (service_order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- Name: ai_daily_report_formal_commits ai_daily_report_formal_commits_draft_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_formal_commits
    ADD CONSTRAINT ai_daily_report_formal_commits_draft_id_fkey FOREIGN KEY (draft_id) REFERENCES public.ai_daily_report_drafts(id) ON DELETE CASCADE;


--
-- Name: ai_daily_report_manifest_roles ai_daily_report_manifest_roles_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_manifest_roles
    ADD CONSTRAINT ai_daily_report_manifest_roles_manifest_id_fkey FOREIGN KEY (manifest_id) REFERENCES public.ai_daily_report_attachment_manifests(manifest_id) ON DELETE CASCADE;


--
-- Name: ai_daily_report_manifest_roles ai_daily_report_manifest_roles_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_manifest_roles
    ADD CONSTRAINT ai_daily_report_manifest_roles_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.ai_daily_report_manifest_sources(source_id) ON DELETE CASCADE;


--
-- Name: ai_daily_report_manifest_sources ai_daily_report_manifest_sources_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_manifest_sources
    ADD CONSTRAINT ai_daily_report_manifest_sources_manifest_id_fkey FOREIGN KEY (manifest_id) REFERENCES public.ai_daily_report_attachment_manifests(manifest_id) ON DELETE CASCADE;


--
-- Name: ai_daily_report_prepared_assets ai_daily_report_prepared_assets_manifest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_daily_report_prepared_assets
    ADD CONSTRAINT ai_daily_report_prepared_assets_manifest_id_fkey FOREIGN KEY (manifest_id) REFERENCES public.ai_daily_report_attachment_manifests(manifest_id) ON DELETE CASCADE;


--
-- Name: asset_events asset_events_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_events
    ADD CONSTRAINT asset_events_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.assets(id) ON DELETE CASCADE;


--
-- Name: asset_events asset_events_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_events
    ADD CONSTRAINT asset_events_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: asset_events asset_events_holder_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_events
    ADD CONSTRAINT asset_events_holder_id_fkey FOREIGN KEY (holder_id) REFERENCES public.users(id);


--
-- Name: asset_events asset_events_service_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_events
    ADD CONSTRAINT asset_events_service_order_id_fkey FOREIGN KEY (service_order_id) REFERENCES public.service_orders(id);


--
-- Name: asset_photos asset_photos_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_photos
    ADD CONSTRAINT asset_photos_asset_id_fkey FOREIGN KEY (asset_id) REFERENCES public.assets(id) ON DELETE CASCADE;


--
-- Name: asset_photos asset_photos_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.asset_photos
    ADD CONSTRAINT asset_photos_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: assets assets_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: assets assets_current_holder_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_current_holder_id_fkey FOREIGN KEY (current_holder_id) REFERENCES public.users(id);


--
-- Name: assets assets_service_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_service_order_id_fkey FOREIGN KEY (service_order_id) REFERENCES public.service_orders(id);


--
-- Name: audit_logs audit_logs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_logs
    ADD CONSTRAINT audit_logs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: bank_accounts bank_accounts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_accounts
    ADD CONSTRAINT bank_accounts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: bank_accounts bank_accounts_initialized_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_accounts
    ADD CONSTRAINT bank_accounts_initialized_by_fkey FOREIGN KEY (initialized_by) REFERENCES public.users(id);


--
-- Name: bank_transactions bank_transactions_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_bank_account_id_fkey FOREIGN KEY (bank_account_id) REFERENCES public.bank_accounts(id);


--
-- Name: bank_transactions bank_transactions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: bank_transactions bank_transactions_matched_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_matched_batch_id_fkey FOREIGN KEY (matched_batch_id) REFERENCES public.employee_payment_batches(id);


--
-- Name: bank_transactions bank_transactions_matched_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_matched_by_fkey FOREIGN KEY (matched_by) REFERENCES public.users(id);


--
-- Name: bank_transactions bank_transactions_matched_payment_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bank_transactions
    ADD CONSTRAINT bank_transactions_matched_payment_order_id_fkey FOREIGN KEY (matched_payment_order_id) REFERENCES public.employee_payment_orders(id);


--
-- Name: clock_in_photos clock_in_photos_buyer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clock_in_photos
    ADD CONSTRAINT clock_in_photos_buyer_id_fkey FOREIGN KEY (buyer_id) REFERENCES public.buyers(id);


--
-- Name: clock_in_photos clock_in_photos_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.clock_in_photos
    ADD CONSTRAINT clock_in_photos_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: commercial_document_rates commercial_document_rates_commercial_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.commercial_document_rates
    ADD CONSTRAINT commercial_document_rates_commercial_document_id_fkey FOREIGN KEY (commercial_document_id) REFERENCES public.commercial_documents(id) ON DELETE CASCADE;


--
-- Name: company_attachments company_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.company_attachments
    ADD CONSTRAINT company_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: contract_attachments contract_attachments_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_attachments
    ADD CONSTRAINT contract_attachments_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id) ON DELETE CASCADE;


--
-- Name: contract_attachments contract_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_attachments
    ADD CONSTRAINT contract_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: contract_rate_items contract_rate_items_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_rate_items
    ADD CONSTRAINT contract_rate_items_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.contract_rate_versions(id) ON DELETE CASCADE;


--
-- Name: contract_rate_versions contract_rate_versions_contract_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_rate_versions
    ADD CONSTRAINT contract_rate_versions_contract_id_fkey FOREIGN KEY (contract_id) REFERENCES public.contracts(id) ON DELETE CASCADE;


--
-- Name: contract_rate_versions contract_rate_versions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contract_rate_versions
    ADD CONSTRAINT contract_rate_versions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: contracts contracts_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: contracts contracts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.contracts
    ADD CONSTRAINT contracts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: country_translations country_translations_country_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.country_translations
    ADD CONSTRAINT country_translations_country_code_fkey FOREIGN KEY (country_code) REFERENCES public.countries(code) ON DELETE CASCADE;


--
-- Name: customer_prepayment_applications customer_prepayment_applications_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayment_applications
    ADD CONSTRAINT customer_prepayment_applications_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: customer_prepayment_applications customer_prepayment_applications_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayment_applications
    ADD CONSTRAINT customer_prepayment_applications_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id);


--
-- Name: customer_prepayment_applications customer_prepayment_applications_posting_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayment_applications
    ADD CONSTRAINT customer_prepayment_applications_posting_event_id_fkey FOREIGN KEY (posting_event_id) REFERENCES public.posting_events(id);


--
-- Name: customer_prepayment_applications customer_prepayment_applications_prepayment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayment_applications
    ADD CONSTRAINT customer_prepayment_applications_prepayment_id_fkey FOREIGN KEY (prepayment_id) REFERENCES public.customer_prepayments(id);


--
-- Name: customer_prepayment_applications customer_prepayment_applications_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayment_applications
    ADD CONSTRAINT customer_prepayment_applications_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: customer_prepayments customer_prepayments_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayments
    ADD CONSTRAINT customer_prepayments_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.clients(id);


--
-- Name: customer_prepayments customer_prepayments_posting_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayments
    ADD CONSTRAINT customer_prepayments_posting_event_id_fkey FOREIGN KEY (posting_event_id) REFERENCES public.posting_events(id);


--
-- Name: customer_prepayments customer_prepayments_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayments
    ADD CONSTRAINT customer_prepayments_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES public.customer_receipts(id);


--
-- Name: customer_prepayments customer_prepayments_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_prepayments
    ADD CONSTRAINT customer_prepayments_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: customer_receipts customer_receipts_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_receipts
    ADD CONSTRAINT customer_receipts_bank_account_id_fkey FOREIGN KEY (bank_account_id) REFERENCES public.bank_accounts(id);


--
-- Name: customer_receipts customer_receipts_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_receipts
    ADD CONSTRAINT customer_receipts_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: customer_receipts customer_receipts_customer_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_receipts
    ADD CONSTRAINT customer_receipts_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.clients(id);


--
-- Name: customer_receipts customer_receipts_posting_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_receipts
    ADD CONSTRAINT customer_receipts_posting_event_id_fkey FOREIGN KEY (posting_event_id) REFERENCES public.posting_events(id);


--
-- Name: customer_receipts customer_receipts_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_receipts
    ADD CONSTRAINT customer_receipts_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: customer_reimbursement_attachments customer_reimbursement_attachmen_customer_reimbursement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_attachments
    ADD CONSTRAINT customer_reimbursement_attachmen_customer_reimbursement_id_fkey FOREIGN KEY (customer_reimbursement_id) REFERENCES public.customer_reimbursements(id) ON DELETE CASCADE;


--
-- Name: customer_reimbursement_attachments customer_reimbursement_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_attachments
    ADD CONSTRAINT customer_reimbursement_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: customer_reimbursement_expense_ignores customer_reimbursement_expense_ignores_customer_reimbursement_i; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_expense_ignores
    ADD CONSTRAINT customer_reimbursement_expense_ignores_customer_reimbursement_i FOREIGN KEY (customer_reimbursement_id) REFERENCES public.customer_reimbursements(id) ON DELETE CASCADE;


--
-- Name: customer_reimbursement_expense_ignores customer_reimbursement_expense_ignores_expense_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_expense_ignores
    ADD CONSTRAINT customer_reimbursement_expense_ignores_expense_item_id_fkey FOREIGN KEY (expense_item_id) REFERENCES public.expense_items(id) ON DELETE RESTRICT;


--
-- Name: customer_reimbursement_expense_links customer_reimbursement_expense_l_customer_reimbursement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_expense_links
    ADD CONSTRAINT customer_reimbursement_expense_l_customer_reimbursement_id_fkey FOREIGN KEY (customer_reimbursement_id) REFERENCES public.customer_reimbursements(id) ON DELETE CASCADE;


--
-- Name: customer_reimbursement_expense_links customer_reimbursement_expense_links_expense_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_expense_links
    ADD CONSTRAINT customer_reimbursement_expense_links_expense_item_id_fkey FOREIGN KEY (expense_item_id) REFERENCES public.expense_items(id) ON DELETE RESTRICT;


--
-- Name: customer_reimbursement_expense_links customer_reimbursement_expense_links_selected_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_expense_links
    ADD CONSTRAINT customer_reimbursement_expense_links_selected_by_fkey FOREIGN KEY (selected_by) REFERENCES public.users(id);


--
-- Name: customer_reimbursement_items customer_reimbursement_items_customer_reimbursement_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursement_items
    ADD CONSTRAINT customer_reimbursement_items_customer_reimbursement_id_fkey FOREIGN KEY (customer_reimbursement_id) REFERENCES public.customer_reimbursements(id) ON DELETE CASCADE;


--
-- Name: customer_reimbursements customer_reimbursements_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursements
    ADD CONSTRAINT customer_reimbursements_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: customer_reimbursements customer_reimbursements_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursements
    ADD CONSTRAINT customer_reimbursements_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE SET NULL;


--
-- Name: customer_reimbursements customer_reimbursements_quotation_revision_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursements
    ADD CONSTRAINT customer_reimbursements_quotation_revision_id_fkey FOREIGN KEY (quotation_revision_id) REFERENCES public.quotation_revisions(id);


--
-- Name: customer_reimbursements customer_reimbursements_service_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.customer_reimbursements
    ADD CONSTRAINT customer_reimbursements_service_order_id_fkey FOREIGN KEY (service_order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- Name: email_delivery_logs email_delivery_logs_sent_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_delivery_logs
    ADD CONSTRAINT email_delivery_logs_sent_by_fkey FOREIGN KEY (sent_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: email_delivery_logs email_delivery_logs_source_audit_log_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.email_delivery_logs
    ADD CONSTRAINT email_delivery_logs_source_audit_log_id_fkey FOREIGN KEY (source_audit_log_id) REFERENCES public.audit_logs(id) ON DELETE SET NULL;


--
-- Name: employee_advance_applications employee_advance_applications_advance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advance_applications
    ADD CONSTRAINT employee_advance_applications_advance_id_fkey FOREIGN KEY (advance_id) REFERENCES public.employee_advances(id);


--
-- Name: employee_advance_applications employee_advance_applications_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advance_applications
    ADD CONSTRAINT employee_advance_applications_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: employee_advance_applications employee_advance_applications_payment_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advance_applications
    ADD CONSTRAINT employee_advance_applications_payment_order_id_fkey FOREIGN KEY (payment_order_id) REFERENCES public.employee_payment_orders(id);


--
-- Name: employee_advance_applications employee_advance_applications_posting_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advance_applications
    ADD CONSTRAINT employee_advance_applications_posting_event_id_fkey FOREIGN KEY (posting_event_id) REFERENCES public.posting_events(id);


--
-- Name: employee_advance_applications employee_advance_applications_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advance_applications
    ADD CONSTRAINT employee_advance_applications_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: employee_advances employee_advances_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advances
    ADD CONSTRAINT employee_advances_bank_account_id_fkey FOREIGN KEY (bank_account_id) REFERENCES public.bank_accounts(id);


--
-- Name: employee_advances employee_advances_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advances
    ADD CONSTRAINT employee_advances_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: employee_advances employee_advances_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_advances
    ADD CONSTRAINT employee_advances_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.users(id);


--
-- Name: employee_payment_batches employee_payment_batches_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_batches
    ADD CONSTRAINT employee_payment_batches_bank_account_id_fkey FOREIGN KEY (bank_account_id) REFERENCES public.bank_accounts(id);


--
-- Name: employee_payment_batches employee_payment_batches_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_batches
    ADD CONSTRAINT employee_payment_batches_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.users(id);


--
-- Name: employee_payment_batches employee_payment_batches_issued_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_batches
    ADD CONSTRAINT employee_payment_batches_issued_by_fkey FOREIGN KEY (issued_by) REFERENCES public.users(id);


--
-- Name: employee_payment_batches employee_payment_batches_reconciled_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_batches
    ADD CONSTRAINT employee_payment_batches_reconciled_by_fkey FOREIGN KEY (reconciled_by) REFERENCES public.users(id);


--
-- Name: employee_payment_batches employee_payment_batches_voided_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_batches
    ADD CONSTRAINT employee_payment_batches_voided_by_fkey FOREIGN KEY (voided_by) REFERENCES public.users(id);


--
-- Name: employee_payment_components employee_payment_components_daily_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_components
    ADD CONSTRAINT employee_payment_components_daily_report_id_fkey FOREIGN KEY (daily_report_id) REFERENCES public.service_reports(id);


--
-- Name: employee_payment_components employee_payment_components_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_components
    ADD CONSTRAINT employee_payment_components_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.users(id);


--
-- Name: employee_payment_components employee_payment_components_payment_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_components
    ADD CONSTRAINT employee_payment_components_payment_order_id_fkey FOREIGN KEY (payment_order_id) REFERENCES public.employee_payment_orders(id) ON DELETE CASCADE;


--
-- Name: employee_payment_components employee_payment_components_work_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_components
    ADD CONSTRAINT employee_payment_components_work_order_id_fkey FOREIGN KEY (work_order_id) REFERENCES public.service_orders(id);


--
-- Name: employee_payment_orders employee_payment_orders_bank_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_bank_account_id_fkey FOREIGN KEY (bank_account_id) REFERENCES public.bank_accounts(id);


--
-- Name: employee_payment_orders employee_payment_orders_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.employee_payment_batches(id);


--
-- Name: employee_payment_orders employee_payment_orders_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: employee_payment_orders employee_payment_orders_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.users(id);


--
-- Name: employee_payment_orders employee_payment_orders_paid_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_paid_by_fkey FOREIGN KEY (paid_by) REFERENCES public.users(id);


--
-- Name: employee_payment_orders employee_payment_orders_primary_advance_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_primary_advance_id_fkey FOREIGN KEY (primary_advance_id) REFERENCES public.employee_advances(id);


--
-- Name: employee_payment_orders employee_payment_orders_reconciled_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_reconciled_by_fkey FOREIGN KEY (reconciled_by) REFERENCES public.users(id);


--
-- Name: employee_payment_orders employee_payment_orders_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT employee_payment_orders_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id);


--
-- Name: employee_payment_tax_reviews employee_payment_tax_reviews_component_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_tax_reviews
    ADD CONSTRAINT employee_payment_tax_reviews_component_id_fkey FOREIGN KEY (component_id) REFERENCES public.employee_payment_components(id) ON DELETE CASCADE;


--
-- Name: employee_payment_tax_reviews employee_payment_tax_reviews_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_tax_reviews
    ADD CONSTRAINT employee_payment_tax_reviews_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id);


--
-- Name: employee_rate_items employee_rate_items_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_rate_items
    ADD CONSTRAINT employee_rate_items_version_id_fkey FOREIGN KEY (version_id) REFERENCES public.employee_rate_versions(id) ON DELETE CASCADE;


--
-- Name: employee_rate_versions employee_rate_versions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_rate_versions
    ADD CONSTRAINT employee_rate_versions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: employee_rate_versions employee_rate_versions_employee_grade_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_rate_versions
    ADD CONSTRAINT employee_rate_versions_employee_grade_id_fkey FOREIGN KEY (employee_grade_id) REFERENCES public.employee_grades(id) ON DELETE CASCADE;


--
-- Name: employee_salary_agreements employee_salary_agreements_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_salary_agreements
    ADD CONSTRAINT employee_salary_agreements_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: employee_salary_agreements employee_salary_agreements_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_salary_agreements
    ADD CONSTRAINT employee_salary_agreements_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.users(id);


--
-- Name: expense_ai_reviews expense_ai_reviews_expense_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_ai_reviews
    ADD CONSTRAINT expense_ai_reviews_expense_id_fkey FOREIGN KEY (expense_id) REFERENCES public.expenses(id) ON DELETE CASCADE;


--
-- Name: expense_attachment_interpretations expense_attachment_interpretations_attachment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_attachment_interpretations
    ADD CONSTRAINT expense_attachment_interpretations_attachment_id_fkey FOREIGN KEY (attachment_id) REFERENCES public.expense_attachments(id) ON DELETE CASCADE;


--
-- Name: expense_attachments expense_attachments_expense_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_attachments
    ADD CONSTRAINT expense_attachments_expense_id_fkey FOREIGN KEY (expense_id) REFERENCES public.expenses(id) ON DELETE CASCADE;


--
-- Name: expense_attachments expense_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_attachments
    ADD CONSTRAINT expense_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: expense_duplicate_checks expense_duplicate_checks_attachment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_duplicate_checks
    ADD CONSTRAINT expense_duplicate_checks_attachment_id_fkey FOREIGN KEY (attachment_id) REFERENCES public.expense_attachments(id) ON DELETE CASCADE;


--
-- Name: expense_duplicate_checks expense_duplicate_checks_expense_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_duplicate_checks
    ADD CONSTRAINT expense_duplicate_checks_expense_id_fkey FOREIGN KEY (expense_id) REFERENCES public.expenses(id) ON DELETE CASCADE;


--
-- Name: expense_duplicate_checks expense_duplicate_checks_matched_attachment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_duplicate_checks
    ADD CONSTRAINT expense_duplicate_checks_matched_attachment_id_fkey FOREIGN KEY (matched_attachment_id) REFERENCES public.expense_attachments(id) ON DELETE CASCADE;


--
-- Name: expense_duplicate_checks expense_duplicate_checks_matched_expense_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_duplicate_checks
    ADD CONSTRAINT expense_duplicate_checks_matched_expense_id_fkey FOREIGN KEY (matched_expense_id) REFERENCES public.expenses(id) ON DELETE CASCADE;


--
-- Name: expense_duplicate_checks expense_duplicate_checks_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_duplicate_checks
    ADD CONSTRAINT expense_duplicate_checks_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id);


--
-- Name: expense_items expense_items_expense_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_items
    ADD CONSTRAINT expense_items_expense_id_fkey FOREIGN KEY (expense_id) REFERENCES public.expenses(id) ON DELETE CASCADE;


--
-- Name: expense_items expense_items_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_items
    ADD CONSTRAINT expense_items_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: expense_save_tokens expense_save_tokens_expense_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expense_save_tokens
    ADD CONSTRAINT expense_save_tokens_expense_id_fkey FOREIGN KEY (expense_id) REFERENCES public.expenses(id) ON DELETE CASCADE;


--
-- Name: expenses expenses_beneficiary_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_beneficiary_id_fkey FOREIGN KEY (beneficiary_id) REFERENCES public.users(id);


--
-- Name: expenses expenses_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: expenses expenses_reviewed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_reviewed_by_fkey FOREIGN KEY (reviewed_by) REFERENCES public.users(id);


--
-- Name: expenses expenses_service_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.expenses
    ADD CONSTRAINT expenses_service_order_id_fkey FOREIGN KEY (service_order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- Name: field_photos field_photos_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_photos
    ADD CONSTRAINT field_photos_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- Name: field_photos field_photos_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.field_photos
    ADD CONSTRAINT field_photos_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: employee_payment_orders fk_employee_payments_correction_group; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT fk_employee_payments_correction_group FOREIGN KEY (correction_group_id) REFERENCES public.payroll_correction_groups(id);


--
-- Name: employee_payment_orders fk_employee_payments_superseded_by; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.employee_payment_orders
    ADD CONSTRAINT fk_employee_payments_superseded_by FOREIGN KEY (superseded_by_id) REFERENCES public.employee_payment_orders(id);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_posting_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_posting_event_id_fkey FOREIGN KEY (posting_event_id) REFERENCES public.posting_events(id);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_reversal_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_reversal_event_id_fkey FOREIGN KEY (reversal_event_id) REFERENCES public.posting_events(id);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_reversal_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_reversal_voucher_id_fkey FOREIGN KEY (reversal_voucher_id) REFERENCES public.vouchers(id);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_reversed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_reversed_by_fkey FOREIGN KEY (reversed_by) REFERENCES public.users(id);


--
-- Name: invoice_accounting_corrections invoice_accounting_corrections_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_accounting_corrections
    ADD CONSTRAINT invoice_accounting_corrections_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: invoice_attachments invoice_attachments_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_attachments
    ADD CONSTRAINT invoice_attachments_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE CASCADE;


--
-- Name: invoice_attachments invoice_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_attachments
    ADD CONSTRAINT invoice_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: invoice_items invoice_items_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE CASCADE;


--
-- Name: invoice_items invoice_items_project_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_items
    ADD CONSTRAINT invoice_items_project_id_fkey FOREIGN KEY (project_id) REFERENCES public.projects(id);


--
-- Name: invoice_save_tokens invoice_save_tokens_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoice_save_tokens
    ADD CONSTRAINT invoice_save_tokens_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE CASCADE;


--
-- Name: invoices invoices_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: invoices invoices_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.invoices
    ADD CONSTRAINT invoices_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: knowledge_document_versions knowledge_document_versions_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_document_versions
    ADD CONSTRAINT knowledge_document_versions_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.knowledge_documents(id) ON DELETE CASCADE;


--
-- Name: knowledge_document_versions knowledge_document_versions_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_document_versions
    ADD CONSTRAINT knowledge_document_versions_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: knowledge_documents knowledge_documents_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_documents
    ADD CONSTRAINT knowledge_documents_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: messages messages_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.messages
    ADD CONSTRAINT messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: payment_order_events payment_order_events_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_order_events
    ADD CONSTRAINT payment_order_events_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: payment_order_events payment_order_events_payment_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_order_events
    ADD CONSTRAINT payment_order_events_payment_order_id_fkey FOREIGN KEY (payment_order_id) REFERENCES public.employee_payment_orders(id) ON DELETE CASCADE;


--
-- Name: payment_order_sources payment_order_sources_payment_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payment_order_sources
    ADD CONSTRAINT payment_order_sources_payment_order_id_fkey FOREIGN KEY (payment_order_id) REFERENCES public.employee_payment_orders(id) ON DELETE CASCADE;


--
-- Name: payroll_component_correction_allocations payroll_component_correction_al_canonical_payment_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_correction_allocations
    ADD CONSTRAINT payroll_component_correction_al_canonical_payment_order_id_fkey FOREIGN KEY (canonical_payment_order_id) REFERENCES public.employee_payment_orders(id);


--
-- Name: payroll_component_correction_allocations payroll_component_correction_allocatio_correction_group_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_correction_allocations
    ADD CONSTRAINT payroll_component_correction_allocatio_correction_group_id_fkey FOREIGN KEY (correction_group_id) REFERENCES public.payroll_correction_groups(id);


--
-- Name: payroll_component_correction_allocations payroll_component_correction_allocatio_keeper_component_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_correction_allocations
    ADD CONSTRAINT payroll_component_correction_allocatio_keeper_component_id_fkey FOREIGN KEY (keeper_component_id) REFERENCES public.employee_payment_components(id);


--
-- Name: payroll_component_correction_allocations payroll_component_correction_allocations_component_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_correction_allocations
    ADD CONSTRAINT payroll_component_correction_allocations_component_id_fkey FOREIGN KEY (component_id) REFERENCES public.employee_payment_components(id);


--
-- Name: payroll_component_correction_allocations payroll_component_correction_allocations_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_correction_allocations
    ADD CONSTRAINT payroll_component_correction_allocations_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: payroll_component_tax_config payroll_component_tax_config_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_component_tax_config
    ADD CONSTRAINT payroll_component_tax_config_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: payroll_correction_groups payroll_correction_groups_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.payroll_correction_groups
    ADD CONSTRAINT payroll_correction_groups_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: posting_audit posting_audit_actor_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posting_audit
    ADD CONSTRAINT posting_audit_actor_id_fkey FOREIGN KEY (actor_id) REFERENCES public.users(id);


--
-- Name: posting_audit posting_audit_posting_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posting_audit
    ADD CONSTRAINT posting_audit_posting_event_id_fkey FOREIGN KEY (posting_event_id) REFERENCES public.posting_events(id);


--
-- Name: posting_audit posting_audit_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posting_audit
    ADD CONSTRAINT posting_audit_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: posting_events posting_events_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posting_events
    ADD CONSTRAINT posting_events_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: posting_events posting_events_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.posting_events
    ADD CONSTRAINT posting_events_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: profit_ledger profit_ledger_contract_rate_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profit_ledger
    ADD CONSTRAINT profit_ledger_contract_rate_version_id_fkey FOREIGN KEY (contract_rate_version_id) REFERENCES public.contract_rate_versions(id);


--
-- Name: profit_ledger profit_ledger_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profit_ledger
    ADD CONSTRAINT profit_ledger_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.users(id);


--
-- Name: profit_ledger profit_ledger_employee_rate_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profit_ledger
    ADD CONSTRAINT profit_ledger_employee_rate_version_id_fkey FOREIGN KEY (employee_rate_version_id) REFERENCES public.employee_rate_versions(id);


--
-- Name: profit_ledger profit_ledger_service_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.profit_ledger
    ADD CONSTRAINT profit_ledger_service_order_id_fkey FOREIGN KEY (service_order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- Name: quotation_revisions quotation_revisions_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotation_revisions
    ADD CONSTRAINT quotation_revisions_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: quotation_revisions quotation_revisions_quotation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotation_revisions
    ADD CONSTRAINT quotation_revisions_quotation_id_fkey FOREIGN KEY (quotation_id) REFERENCES public.quotations(id) ON DELETE CASCADE;


--
-- Name: quotations quotations_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotations
    ADD CONSTRAINT quotations_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: quotations quotations_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.quotations
    ADD CONSTRAINT quotations_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: receipt_allocations receipt_allocations_invoice_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipt_allocations
    ADD CONSTRAINT receipt_allocations_invoice_id_fkey FOREIGN KEY (invoice_id) REFERENCES public.invoices(id);


--
-- Name: receipt_allocations receipt_allocations_receipt_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipt_allocations
    ADD CONSTRAINT receipt_allocations_receipt_id_fkey FOREIGN KEY (receipt_id) REFERENCES public.customer_receipts(id);


--
-- Name: receipt_allocations receipt_allocations_redirected_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipt_allocations
    ADD CONSTRAINT receipt_allocations_redirected_to_fkey FOREIGN KEY (redirected_to) REFERENCES public.receipt_allocations(id);


--
-- Name: receipt_allocations receipt_allocations_reversed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipt_allocations
    ADD CONSTRAINT receipt_allocations_reversed_by_fkey FOREIGN KEY (reversed_by) REFERENCES public.receipt_allocations(id);


--
-- Name: receipt_allocations receipt_allocations_source_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.receipt_allocations
    ADD CONSTRAINT receipt_allocations_source_event_id_fkey FOREIGN KEY (source_event_id) REFERENCES public.posting_events(id);


--
-- Name: role_action_permissions role_action_permissions_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_action_permissions
    ADD CONSTRAINT role_action_permissions_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: role_menu_permissions role_menu_permissions_updated_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.role_menu_permissions
    ADD CONSTRAINT role_menu_permissions_updated_by_fkey FOREIGN KEY (updated_by) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: service_orders service_orders_commercial_document_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_commercial_document_fkey FOREIGN KEY (commercial_document_id) REFERENCES public.commercial_documents(id);


--
-- Name: service_orders service_orders_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: service_orders service_orders_quotation_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_orders
    ADD CONSTRAINT service_orders_quotation_id_fkey FOREIGN KEY (quotation_id) REFERENCES public.quotations(id);


--
-- Name: service_report_attachments service_report_attachments_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_attachments
    ADD CONSTRAINT service_report_attachments_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.service_reports(id) ON DELETE CASCADE;


--
-- Name: service_report_attachments service_report_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_attachments
    ADD CONSTRAINT service_report_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: service_report_mileage_evidence service_report_mileage_evidence_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_mileage_evidence
    ADD CONSTRAINT service_report_mileage_evidence_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.service_reports(id) ON DELETE CASCADE;


--
-- Name: service_report_replaced_parts service_report_replaced_parts_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_replaced_parts
    ADD CONSTRAINT service_report_replaced_parts_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.service_reports(id) ON DELETE CASCADE;


--
-- Name: service_report_save_tokens service_report_save_tokens_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_save_tokens
    ADD CONSTRAINT service_report_save_tokens_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.service_reports(id) ON DELETE CASCADE;


--
-- Name: service_report_saved_parts service_report_saved_parts_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_saved_parts
    ADD CONSTRAINT service_report_saved_parts_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.service_reports(id) ON DELETE CASCADE;


--
-- Name: service_report_workers service_report_workers_report_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_workers
    ADD CONSTRAINT service_report_workers_report_id_fkey FOREIGN KEY (report_id) REFERENCES public.service_reports(id) ON DELETE CASCADE;


--
-- Name: service_report_workers service_report_workers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_report_workers
    ADD CONSTRAINT service_report_workers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: service_reports service_reports_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_reports
    ADD CONSTRAINT service_reports_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: service_reports service_reports_service_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.service_reports
    ADD CONSTRAINT service_reports_service_order_id_fkey FOREIGN KEY (service_order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- Name: user_attachments user_attachments_uploaded_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_attachments
    ADD CONSTRAINT user_attachments_uploaded_by_fkey FOREIGN KEY (uploaded_by) REFERENCES public.users(id);


--
-- Name: user_attachments user_attachments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_attachments
    ADD CONSTRAINT user_attachments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: user_service_orders user_service_orders_assigned_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_service_orders
    ADD CONSTRAINT user_service_orders_assigned_by_fkey FOREIGN KEY (assigned_by) REFERENCES public.users(id);


--
-- Name: user_service_orders user_service_orders_service_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_service_orders
    ADD CONSTRAINT user_service_orders_service_order_id_fkey FOREIGN KEY (service_order_id) REFERENCES public.service_orders(id) ON DELETE CASCADE;


--
-- Name: user_service_orders user_service_orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_service_orders
    ADD CONSTRAINT user_service_orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: users users_client_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_client_id_fkey FOREIGN KEY (client_id) REFERENCES public.clients(id);


--
-- Name: voucher_attachment_links voucher_attachment_links_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_attachment_links
    ADD CONSTRAINT voucher_attachment_links_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: voucher_entries voucher_entries_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_entries
    ADD CONSTRAINT voucher_entries_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id);


--
-- Name: voucher_entries voucher_entries_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_entries
    ADD CONSTRAINT voucher_entries_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: voucher_settlement_lines voucher_settlement_lines_redirected_to_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_settlement_lines
    ADD CONSTRAINT voucher_settlement_lines_redirected_to_fkey FOREIGN KEY (redirected_to) REFERENCES public.voucher_settlement_lines(id);


--
-- Name: voucher_settlement_lines voucher_settlement_lines_reversed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_settlement_lines
    ADD CONSTRAINT voucher_settlement_lines_reversed_by_fkey FOREIGN KEY (reversed_by) REFERENCES public.voucher_settlement_lines(id);


--
-- Name: voucher_settlement_lines voucher_settlement_lines_source_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_settlement_lines
    ADD CONSTRAINT voucher_settlement_lines_source_event_id_fkey FOREIGN KEY (source_event_id) REFERENCES public.posting_events(id);


--
-- Name: voucher_settlement_lines voucher_settlement_lines_voucher_entry_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_settlement_lines
    ADD CONSTRAINT voucher_settlement_lines_voucher_entry_id_fkey FOREIGN KEY (voucher_entry_id) REFERENCES public.voucher_entries(id);


--
-- Name: voucher_settlement_lines voucher_settlement_lines_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_settlement_lines
    ADD CONSTRAINT voucher_settlement_lines_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: voucher_source_links voucher_source_links_voucher_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.voucher_source_links
    ADD CONSTRAINT voucher_source_links_voucher_id_fkey FOREIGN KEY (voucher_id) REFERENCES public.vouchers(id);


--
-- Name: vouchers vouchers_corrects_opening_line_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_corrects_opening_line_fkey FOREIGN KEY (corrects_opening_line) REFERENCES public.accounting_opening_lines(id);


--
-- Name: vouchers vouchers_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: vouchers vouchers_posted_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_posted_by_fkey FOREIGN KEY (posted_by) REFERENCES public.users(id);


--
-- Name: vouchers vouchers_reversal_of_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_reversal_of_fkey FOREIGN KEY (reversal_of) REFERENCES public.vouchers(id);


--
-- Name: vouchers vouchers_reversed_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.vouchers
    ADD CONSTRAINT vouchers_reversed_by_fkey FOREIGN KEY (reversed_by) REFERENCES public.users(id);


--
-- Name: worker_tax_status_history worker_tax_status_history_created_by_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_tax_status_history
    ADD CONSTRAINT worker_tax_status_history_created_by_fkey FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: worker_tax_status_history worker_tax_status_history_employee_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_tax_status_history
    ADD CONSTRAINT worker_tax_status_history_employee_id_fkey FOREIGN KEY (employee_id) REFERENCES public.users(id);


--
-- PostgreSQL database dump complete
--

\unrestrict jRFH8isCbGlVw27RdBw6ucAcVCH3EtujX4xQ2wQPNa3DDgHD22hpD7v2ODwPM1N

