/**
 * Migration: Create users table and update related schemas
 *
 * This migration:
 * 1. Creates the new users table with auth + onboarding fields
 * 2. Updates dogs and documents tables to reference users instead of owners
 * 3. Sets up RLS policies for users table
 */

-- Step 1: Create users table
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  email TEXT NOT NULL UNIQUE,

  -- Onboarding data
  is_owner BOOLEAN,
  roles TEXT[] DEFAULT '{}',
  experience_level TEXT,
  primary_use TEXT,

  -- Location
  location_address TEXT,
  location_lat FLOAT8,
  location_lng FLOAT8,

  -- Professional details
  org_name TEXT,
  org_location_address TEXT,
  org_location_lat FLOAT8,
  org_location_lng FLOAT8,

  -- Preferences
  data_sharing_preference TEXT,

  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),

  CONSTRAINT valid_experience CHECK (experience_level IN ('low', 'medium', 'high')),
  CONSTRAINT valid_primary_use CHECK (primary_use IN ('health_tracking', 'operations', 'casual')),
  CONSTRAINT valid_data_sharing CHECK (data_sharing_preference IN ('private', 'org', 'public'))
);

-- Step 2: Enable RLS on users table
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- Step 3: Create RLS policies for users
-- Users can read their own row
CREATE POLICY "users_read_own" ON users
  FOR SELECT
  USING (auth.uid() = id);

-- Users can update their own row
CREATE POLICY "users_update_own" ON users
  FOR UPDATE
  USING (auth.uid() = id);

-- Insert only works on signup (via auth trigger or backend endpoint)
CREATE POLICY "users_insert_own" ON users
  FOR INSERT
  WITH CHECK (auth.uid() = id);

-- Step 4: Create indexes
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);

-- Step 5: Update dogs table to reference users
-- Safe approach: handle both cases (owner_id exists or doesn't)
DO $$
BEGIN
  -- Try to rename owner_id to user_id if it exists
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'dogs' AND column_name = 'owner_id'
  ) THEN
    ALTER TABLE dogs RENAME COLUMN owner_id TO user_id;
  ELSIF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'dogs' AND column_name = 'user_id'
  ) THEN
    -- If neither exists, create user_id column
    ALTER TABLE dogs ADD COLUMN user_id UUID;
  END IF;
END $$;

-- Add FK constraint if not already present
ALTER TABLE dogs
  ADD CONSTRAINT fk_dogs_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

-- Create index on user_id for query performance
CREATE INDEX IF NOT EXISTS idx_dogs_user_id ON dogs(user_id);

-- Step 6: Update documents table to reference users
-- Add user_id column if it doesn't exist (documents table may not have had owner tracking)
ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;

-- Create index on user_id for query performance
CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);

-- Step 7: Create a trigger to auto-create users row on auth.users creation
-- (Optional - you can also create users row in backend)
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  INSERT INTO public.users (id, email)
  VALUES (NEW.id, NEW.email)
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

-- Drop trigger if it exists
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

-- Create trigger
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();

-- Step 8: (Optional) Migrate existing data from old schema
-- If you had an owners table, uncomment below to migrate:
-- INSERT INTO users (id, email, created_at, updated_at)
-- SELECT id, email, created_at, updated_at FROM owners
-- ON CONFLICT (id) DO NOTHING;
