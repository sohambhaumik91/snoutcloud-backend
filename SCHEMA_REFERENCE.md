#registrations table schema

column_name,data_type
registration_id,uuid
user_id,uuid
dog_id,uuid
job_id,uuid
attempt_number,integer
status,USER-DEFINED
expected_frames,integer
created_at,timestamp with time zone
updated_at,timestamp with time zone
expires_at,timestamp with time zone
matched_dog_id,uuid
match_score,double precision



my backend claude.md has become old and not aligned with what i want to do.

write upddated claude.md file that talks about the following tasks primarily:





the nose crop flow: client requests for presigned urls for all nose crop images. fastapi endpoint receives client request, fetches presigned urls from supabase and forwards them to client.



client fires

