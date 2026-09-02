-- Cruz Roja WhatsApp Agent — database schema.
-- Idempotent: safe to run repeatedly, and safe to run over the earlier version
-- of this schema (new columns are added, existing data is kept).
-- Run in the Supabase SQL editor, then: python ingest.py

create extension if not exists vector;
create extension if not exists pg_trgm;
create extension if not exists unaccent;

-- Accent- and case-insensitive comparison helper, so "primeros auxilos" and
-- "Primeros Auxilios" match, and "María" survives being typed as "Maria".
create or replace function unaccent_lower(t text) returns text
    language sql stable parallel safe
as $$ select lower(public.unaccent('public.unaccent'::regdictionary, coalesce(t, ''))) $$;

-- ---------------------------------------------------------------------------
-- 1. courses — the structured catalogue, one row per course.
-- This is the source of truth for facts the agent must never improvise:
-- price, hours, prerequisites, credential. The generated answer is checked
-- against this table before it is sent (see core/verify.py).
-- ---------------------------------------------------------------------------
create table if not exists courses (
    course_id            text primary key,
    name_es              text not null,
    name_en              text not null,
    short_label          text not null,          -- <=24 chars, for WhatsApp list rows
    program_track        text,
    category             text not null,
    menu_group           text not null,          -- public | employees | health | rescue | diploma | certification
    short_description    text,
    target_audience      text,
    prerequisites        text,
    minimum_age          text,
    required_education   text,
    contact_hours        text,
    duration_days        text,
    calendar_span        text,
    schedule_format      text,
    delivery_mode        text,
    language             text,
    materials            text,
    assessment           text,
    passing_score        text,
    max_participants     text,
    min_participants     text,
    credential           text,
    price_mxn            numeric,                -- null when pricing is package/monthly based
    price_display        text,                   -- always safe to quote verbatim
    compliance_flags     text,
    updated_at           timestamptz not null default now()
);

create index if not exists courses_menu_group_idx on courses (menu_group);
create index if not exists courses_name_trgm_idx on courses using gin (name_es gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- 2. course_chunks — one retrievable card per course (plus institutional cards).
-- ---------------------------------------------------------------------------
create table if not exists course_chunks (
    id        bigint generated always as identity primary key,
    category  text not null,
    title     text not null,
    content   text not null,
    embedding vector(768) not null
);

alter table course_chunks add column if not exists course_id text;
alter table course_chunks add column if not exists metadata jsonb not null default '{}'::jsonb;

create index if not exists course_chunks_course_id_idx on course_chunks (course_id);
create index if not exists course_chunks_content_trgm_idx on course_chunks using gin (content gin_trgm_ops);
-- Cosine HNSW index. NOTE: a schema migration tool can silently drop this and
-- nothing visibly breaks — search just gets slow. Assert it exists on boot.
create index if not exists course_chunks_embedding_idx
    on course_chunks using hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- 3. leads — one row per WhatsApp contact, holding conversation state.
-- ---------------------------------------------------------------------------
create table if not exists leads (
    id                   uuid primary key default gen_random_uuid(),
    phone                text not null unique,
    name                 text,
    state                text not null default 'GREET',
    enrollment_draft     jsonb not null default '{}'::jsonb,
    history              jsonb not null default '[]'::jsonb,
    language             text not null default 'es',
    pushed_to_dashboard  boolean not null default false,
    dashboard_push_error text,
    created_at           timestamptz not null default now(),
    last_seen_at         timestamptz not null default now()
);

-- Which menu the person is looking at, and where they are in a paged list.
alter table leads add column if not exists menu_state jsonb not null default '{}'::jsonb;
-- Set when a human on the dashboard takes the conversation over; the bot stays quiet.
alter table leads add column if not exists human_takeover boolean not null default false;

-- The table predates this schema, where the default was 'en'. A Mexican client
-- starts in Spanish; existing rows keep whatever they already had.
alter table leads alter column language set default 'es';

create index if not exists leads_last_seen_idx on leads (last_seen_at desc);

-- ---------------------------------------------------------------------------
-- 3b. processed_messages — WhatsApp message ids already handled.
-- Meta's webhook delivery is "at least once": the same message_id can arrive
-- more than once (commonly when the recipient's phone was offline). Claiming
-- the id here before acting on it is what makes a redelivery a no-op instead
-- of a second reply.
-- ---------------------------------------------------------------------------
create table if not exists processed_messages (
    message_id   text primary key,
    processed_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 4. unanswered — questions retrieval could not confidently answer.
-- The list Cruz Roja works through to improve their own material.
-- ---------------------------------------------------------------------------
create table if not exists unanswered_questions (
    id         bigint generated always as identity primary key,
    phone      text,
    question   text not null,
    top_score  double precision,
    language   text,
    created_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- 5. Vector search. Dropped first because the return type changed.
-- ---------------------------------------------------------------------------
drop function if exists match_course_chunks(vector, int);
drop function if exists match_course_chunks(vector, int, text);

create or replace function match_course_chunks(
    query_embedding vector(768),
    match_count int default 5,
    filter_group text default null
)
returns table (
    course_id  text,
    title      text,
    content    text,
    category   text,
    metadata   jsonb,
    similarity double precision
)
language sql stable
as $$
    select
        c.course_id,
        c.title,
        c.content,
        c.category,
        c.metadata,
        1 - (c.embedding <=> query_embedding) as similarity
    from course_chunks c
    where filter_group is null or c.metadata->>'menu_group' = filter_group
    order by c.embedding <=> query_embedding
    limit match_count;
$$;
