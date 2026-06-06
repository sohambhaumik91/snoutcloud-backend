# models/

Place the nose-encoder checkpoint here before building the Docker image:

```
models/best_supcon_clahe_gem.pt
```

The Dockerfile bakes this file into the image (`COPY models/best_supcon_clahe_gem.pt ...`),
so the build **fails fast** if it's missing. The `.pt` itself is git-ignored
(too large to commit; Supabase Storage's 50MB upload cap also rules out
download-at-startup for the ~380MB checkpoint).

The worker loads it from `NOSE_MODEL_PATH` (set to `/app/models/best_supcon_clahe_gem.pt`
in docker-compose). For local (non-Docker) dev, point `NOSE_MODEL_PATH` at
wherever your local copy lives (default in `config.py` is `../best_supcon_clahe_gem.pt`).
