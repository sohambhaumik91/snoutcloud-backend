"""NoseEncoder — inference-only copy of the architecture from the training repo
(D:/biometrics_v2/model.py). SupConLoss is intentionally omitted: backend never
trains, only embeds. Keep this file in sync with the training repo whenever the
encoder architecture changes."""

import torch
import torch.nn as nn
import torch.nn.functional as F
from torchvision.models import convnext_tiny, ConvNeXt_Tiny_Weights


class SelfAttentionBlock(nn.Module):
    """Pre-norm transformer block: LN → MHSA → residual → LN → MLP → residual."""

    def __init__(self, embed_dim: int, n_heads: int, mlp_ratio: float = 4.0, dropout: float = 0.0):
        super().__init__()
        self.norm1 = nn.LayerNorm(embed_dim)
        self.attn = nn.MultiheadAttention(embed_dim, n_heads, dropout=dropout, batch_first=True)
        self.norm2 = nn.LayerNorm(embed_dim)
        mlp_dim = int(embed_dim * mlp_ratio)
        self.mlp = nn.Sequential(
            nn.Linear(embed_dim, mlp_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(mlp_dim, embed_dim),
            nn.Dropout(dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        normed = self.norm1(x)
        attn_out, _ = self.attn(normed, normed, normed)
        x = x + attn_out
        x = x + self.mlp(self.norm2(x))
        return x


class LearnablePositionEmbedding(nn.Module):
    def __init__(self, n_tokens: int, embed_dim: int, include_cls: bool = True):
        super().__init__()
        total = n_tokens + 1 if include_cls else n_tokens
        self.pos_embed = nn.Parameter(torch.zeros(1, total, embed_dim))
        nn.init.trunc_normal_(self.pos_embed, std=0.02)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.pos_embed


class GeM(nn.Module):
    """Generalized Mean Pooling: (mean(x^p))^(1/p), p learnable."""

    def __init__(self, p_init: float = 3.0, eps: float = 1e-6):
        super().__init__()
        self.p = nn.Parameter(torch.ones(1) * p_init)
        self.eps = eps

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x.clamp(min=self.eps).pow(self.p).mean(dim=1).pow(1.0 / self.p)


class AttentionHead(nn.Module):
    def __init__(
        self,
        in_channels: int,
        embed_dim: int,
        n_heads: int,
        n_layers: int,
        n_tokens: int,
        pool: str = "cls",
        dropout: float = 0.0,
    ):
        super().__init__()
        assert pool in ("cls", "gem"), f"pool must be 'cls' or 'gem', got '{pool}'"
        self.pool = pool
        self.proj = nn.Linear(in_channels, embed_dim)

        if pool == "cls":
            self.cls_token = nn.Parameter(torch.zeros(1, 1, embed_dim))
            nn.init.trunc_normal_(self.cls_token, std=0.02)
            self.pos_embed = LearnablePositionEmbedding(n_tokens, embed_dim, include_cls=True)
        else:
            self.pos_embed = LearnablePositionEmbedding(n_tokens, embed_dim, include_cls=False)
            self.gem = GeM(p_init=3.0)

        self.blocks = nn.ModuleList(
            [SelfAttentionBlock(embed_dim, n_heads, dropout=dropout) for _ in range(n_layers)]
        )
        self.norm = nn.LayerNorm(embed_dim)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        B, C, H, W = features.shape
        x = features.flatten(2).transpose(1, 2)
        x = self.proj(x)

        if self.pool == "cls":
            cls = self.cls_token.expand(B, -1, -1)
            x = torch.cat([cls, x], dim=1)
            x = self.pos_embed(x)
            for blk in self.blocks:
                x = blk(x)
            x = self.norm(x)
            return x[:, 0]
        else:
            x = self.pos_embed(x)
            for blk in self.blocks:
                x = blk(x)
            x = self.norm(x)
            return self.gem(x)


class NoseEncoder(nn.Module):
    CONVNEXT_DIM = 768
    GLOBAL_GRID = 7 * 7

    def __init__(
        self,
        embed_dim: int = 384,
        proj_dim: int = 128,
        n_heads: int = 6,
        n_layers: int = 2,
        pool: str = "cls",
        pretrained: bool = False,  # checkpoint will overwrite; skip ImageNet download
    ):
        super().__init__()
        assert embed_dim % n_heads == 0
        weights = ConvNeXt_Tiny_Weights.IMAGENET1K_V1 if pretrained else None
        self.backbone = convnext_tiny(weights=weights).features
        self.attn_head = AttentionHead(
            in_channels=self.CONVNEXT_DIM,
            embed_dim=embed_dim,
            n_heads=n_heads,
            n_layers=n_layers,
            n_tokens=self.GLOBAL_GRID,
            pool=pool,
        )
        self.proj_head = nn.Sequential(
            nn.Linear(embed_dim, embed_dim),
            nn.LayerNorm(embed_dim),
            nn.ReLU(),
            nn.Linear(embed_dim, proj_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.proj_head(self._encode(x)), dim=-1)

    def embed(self, x: torch.Tensor) -> torch.Tensor:
        """Retrieval path — L2-normalized backbone embedding (no proj head)."""
        return F.normalize(self._encode(x), dim=-1)

    def _encode(self, x: torch.Tensor) -> torch.Tensor:
        return self.attn_head(self.backbone(x))
