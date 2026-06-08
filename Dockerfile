FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
# Install CPU-only torch/torchvision FIRST from the PyTorch CPU index. Inference
# runs on CPU, so this avoids pulling the ~2.5GB CUDA stack (nvidia-* wheels)
# that the default PyPI torch wheel drags in. The main install below then sees
# torch already satisfied and only fetches the remaining deps.
RUN pip install --no-cache-dir torch==2.4.1 torchvision==0.19.1 \
    --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download model weights at build time (~90MB)
# so the first request doesn't pay the download cost
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Download the nose-encoder checkpoint (~380MB) from HuggingFace at build time.
# Kept in its OWN layer so editing app code doesn't re-download on every build.
# Repo: https://huggingface.co/mldawg/nosedetectorv1
RUN mkdir -p models && \
    pip install -q huggingface_hub && \
    python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='mldawg/nosedetectorv1', filename='best_supcon_clahe_gem.pt', local_dir='models')"

# App code last — small, changes often, kept off the heavy layers above.
COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
