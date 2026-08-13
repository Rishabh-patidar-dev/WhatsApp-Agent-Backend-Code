-- Run this once in the Supabase SQL Editor before using ingest.py / server.py.
-- Uses vector(768) to match Gemini's gemini-embedding-001 (output_dimensionality=768).

create extension if not exists vector;

create table if not exists course_chunks (
    id bigint generated always as identity primary key,
    category text not null,
    title text not null,
    content text not null,
    embedding vector(768) not null
);

create table if not exists leads (
    id uuid primary key default gen_random_uuid(),
    phone text not null unique,
    name text,
    -- Conversation state machine: GREET, CHATTING, ENROLL_COURSE, ENROLL_NAME,
    -- ENROLL_EMAIL, ENROLL_PHONE, ENROLL_ADDRESS.
    state text not null default 'GREET',
    -- Fields captured mid-enrollment (course/name/email/phone/address), pushed
    -- to the dashboard and cleared once the flow completes.
    enrollment_draft jsonb not null default '{}'::jsonb,
    history jsonb not null default '[]'::jsonb,
    language text not null default 'en',
    pushed_to_dashboard boolean not null default false,
    -- Raw error (HTTP status + body, or exception text) from the last dashboard
    -- push attempt. NULL on success. Lets us diagnose push failures via a
    -- direct DB query instead of needing Render's log console.
    dashboard_push_error text,
    created_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now()
);

create or replace function match_course_chunks(query_embedding vector(768), match_count int)
returns table (
    title text,
    content text,
    similarity float
)
language sql stable
as $$
    select
        title,
        content,
        1 - (embedding <=> query_embedding) as similarity
    from course_chunks
    order by embedding <=> query_embedding
    limit match_count;
$$;
