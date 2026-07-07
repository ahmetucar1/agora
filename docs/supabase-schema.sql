create extension if not exists pgcrypto;

create table if not exists public.categories (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  image text,
  sort_order integer not null default 0,
  created_at timestamptz not null default now()
);

create table if not exists public.products (
  id uuid primary key default gen_random_uuid(),
  category_id uuid not null references public.categories(id) on delete cascade,
  name text not null,
  price numeric(10,2) not null default 0,
  image text,
  sort_order integer not null default 0,
  created_at timestamptz not null default now()
);

create index if not exists idx_products_category_sort on public.products(category_id, sort_order);

alter table public.categories enable row level security;
alter table public.products enable row level security;

-- Public read policies
create policy if not exists "public read categories"
  on public.categories for select
  using (true);

create policy if not exists "public read products"
  on public.products for select
  using (true);

-- Insert current menu data manually from data/products.json or via Supabase import.
