-- Migration 005: dog profile fields + dog_photos table + dog-photos bucket

-- Add profile columns to dogs table
ALTER TABLE public.dogs ADD COLUMN IF NOT EXISTS breed TEXT;
ALTER TABLE public.dogs ADD COLUMN IF NOT EXISTS age_text TEXT;
ALTER TABLE public.dogs ADD COLUMN IF NOT EXISTS color TEXT;
ALTER TABLE public.dogs ADD COLUMN IF NOT EXISTS bio TEXT;
ALTER TABLE public.dogs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- dog_photos: one row per slot per dog
CREATE TABLE IF NOT EXISTS public.dog_photos (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dog_id       UUID NOT NULL REFERENCES public.dogs(id) ON DELETE CASCADE,
  slot         TEXT NOT NULL CHECK (slot IN ('main', 'secondary_1', 'secondary_2')),
  storage_path TEXT NOT NULL,
  url          TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (dog_id, slot)
);

-- Storage bucket for dog photos (public read, authenticated write)
INSERT INTO storage.buckets (id, name, public)
VALUES ('dog-photos', 'dog-photos', true)
ON CONFLICT (id) DO NOTHING;

-- RLS: authenticated users can upload/delete only their own dog photos
CREATE POLICY "owner insert" ON storage.objects
  FOR INSERT TO authenticated
  WITH CHECK (bucket_id = 'dog-photos');

CREATE POLICY "owner delete" ON storage.objects
  FOR DELETE TO authenticated
  USING (bucket_id = 'dog-photos' AND auth.uid()::text = (storage.foldername(name))[1]);
