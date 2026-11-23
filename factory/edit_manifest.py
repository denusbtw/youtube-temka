"""Unified Versioned EditManifest, Source-Aware Smart Layouts & Safe-Area Geometry (Phase 6).

Single deterministic source of truth driving both browser preview and FFmpeg rendering.
Adapts MIT OpenShorts geometry/framing/placement principles (reframe_v2, split_layout,
hook_placement, edit_builder) cleanly for local CPU/GPU deployment without PyTorch/MediaPipe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


LayoutKind = Literal[
    'fullscreen_smart_crop',
    'source_fit_blur',
    'gameplay_facecam',
    'gameplay_split',
    'two_speaker_split',
    'center_crop',
    'landscape_standard',
]

DesignPresetName = Literal[
    'gaming_clean',
    'gaming_dynamic',
    'streamer_reaction',
    'podcast_premium',
    'satisfying_visual',
]


DESIGN_PRESETS: dict[str, dict[str, Any]] = {
    'gaming_clean': {
        'label': 'Gaming Clean',
        'preferred_layout': 'source_fit_blur',
        'hook_font': 'sans',
        'hook_font_size': 52,
        'hook_color': '#FFFFFF',
        'hook_outline': 3,
        'hook_shadow': True,
        'hook_box': False,
        'hook_position': 'top',
        'caption_style': 'clean',
        'allow_punch_in': False,
    },
    'gaming_dynamic': {
        'label': 'Gaming Dynamic',
        'preferred_layout': 'source_fit_blur',
        'hook_font': 'sans',
        'hook_font_size': 58,
        'hook_color': '#FFFFFF',
        'hook_outline': 4,
        'hook_shadow': True,
        'hook_box': False,
        'hook_position': 'top',
        'caption_style': 'pop',
        'allow_punch_in': True,
    },
    'streamer_reaction': {
        'label': 'Streamer Reaction',
        'preferred_layout': 'gameplay_facecam',
        'hook_font': 'sans',
        'hook_font_size': 50,
        'hook_color': '#FFFFFF',
        'hook_outline': 3,
        'hook_shadow': True,
        'hook_box': False,
        'hook_position': 'top',
        'caption_style': 'pop',
        'allow_punch_in': False,
    },
    'podcast_premium': {
        'label': 'Podcast Premium',
        'preferred_layout': 'fullscreen_smart_crop',
        'hook_font': 'sans',
        'hook_font_size': 48,
        'hook_color': '#FFFFFF',
        'hook_outline': 2,
        'hook_shadow': True,
        'hook_box': False,
        'hook_position': 'top',
        'caption_style': 'clean',
        'allow_punch_in': False,
    },
    'satisfying_visual': {
        'label': 'Satisfying / Visual',
        'preferred_layout': 'source_fit_blur',
        'hook_font': 'serif',
        'hook_font_size': 44,
        'hook_color': '#FFFFFF',
        'hook_outline': 2,
        'hook_shadow': True,
        'hook_box': False,
        'hook_position': 'top',
        'caption_style': 'clean',
        'allow_punch_in': False,
    },
}


class PanelBounds(BaseModel):
    x: int = 0
    y: int = 0
    width: int = 1080
    height: int = 1920


class MotionEffect(BaseModel):
    type: Literal['punch_in', 'zoom_pulse'] = 'punch_in'
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    strength: float = Field(default=0.06, ge=0.01, le=0.12)
    reason: str = ''


class EditManifest(BaseModel):
    schema_version: Literal[1] = 1
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    duration: float = Field(gt=0)
    internal_cuts: list[float] = Field(default_factory=list)
    noncontiguous_joins: list[dict[str, float]] = Field(default_factory=list)
    output_width: int = 1080
    output_height: int = 1920
    layout: LayoutKind = 'source_fit_blur'
    layout_reason: str = ''
    design_preset: DesignPresetName | None = None
    primary_panel: PanelBounds = Field(default_factory=PanelBounds)
    secondary_panel: PanelBounds | None = None
    facecam_corner: Literal['top_left', 'top_right', 'bottom_left', 'bottom_right'] | None = None
    secondary_asset: str | None = None
    split_crop: Literal['fill', 'fit', 'blurred'] = 'blurred'
    hook_text: str = ''
    hook_mode: Literal['persistent', 'timed', 'intro', 'none'] = 'persistent'
    hook_position: Literal['top', 'center', 'bottom'] = 'top'
    hook_anchor_y: float = 160.0
    hook_font: Literal['sans', 'serif', 'mono'] = 'sans'
    hook_font_size: int = 54
    hook_color: str = '#FFFFFF'
    hook_outline: int = 3
    hook_shadow: bool = True
    hook_box: bool = False
    captions_enabled: bool = True
    caption_style: Literal['pop', 'karaoke', 'clean'] = 'pop'
    caption_margin: float = 0.16
    safe_area_top: float = 0.08
    safe_area_bottom: float = 0.82
    audio_mode: Literal['original', 'music', 'silent', 'voiceover'] = 'original'
    secondary_audio_muted: bool = True
    motion_effects: list[MotionEffect] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


def choose_smart_layout(
    source_width: int,
    source_height: int,
    source_category: str = 'auto',
    requested_preset: str = 'auto',
    has_explicit_background: bool = False,
    facecam_corners: list[str] | None = None,
    has_center_face: bool = False,
    two_speakers_detected: bool = False,
    mode: str = 'shorts',
) -> tuple[LayoutKind, str, str | None]:
    """Select an editorially appropriate vertical layout based on source media."""
    if mode == 'longs':
        return 'landscape_standard', 'Long-form landscape output requested.', None

    aspect = (source_width / float(source_height)) if source_width > 0 and source_height > 0 else 16 / 9
    fc = [c for c in (facecam_corners or []) if c in ('top_left', 'top_right', 'bottom_left', 'bottom_right')]

    # Explicit user overrides always win
    if requested_preset == 'gameplay' and has_explicit_background:
        return 'gameplay_split', 'Operator explicitly selected split-screen with secondary asset.', None
    if requested_preset == 'crop':
        return 'center_crop', 'Operator explicitly selected center crop.', None
    if requested_preset == 'blur':
        return 'source_fit_blur', 'Operator explicitly selected full-frame blurred fit.', None
    if requested_preset == 'smart':
        if aspect <= 0.62:
            return 'fullscreen_smart_crop', 'Source is already vertical; preserving full frame.', None
        if two_speakers_detected:
            return 'two_speaker_split', 'Two seated speakers detected for stacked split layout.', None
        if has_center_face:
            return 'fullscreen_smart_crop', 'Face detected; scene-aware smart crop tracks active subject.', None
        return 'source_fit_blur', 'No central face detected; preserving full landscape width over blurred backdrop.', None

    # Auto (source-aware) mode: NEVER forces 65/35 bouncing-ball split unless explicit background requested!
    if has_explicit_background:
        return 'gameplay_split', 'Explicit secondary background asset selected.', None
    if aspect <= 0.62:
        return 'fullscreen_smart_crop', 'Source is already vertical (9:16); full-screen pass-through.', None
    if fc and source_category in ('gameplay', 'stream', 'auto'):
        return 'gameplay_facecam', f'Detected streamer facecam ({fc[0]}); composing facecam + full gameplay.', fc[0]
    if two_speakers_detected and source_category in ('podcast', 'stream', 'auto'):
        return 'two_speaker_split', 'Two coexisting speakers detected; using stacked two-speaker split.', None
    if source_category in ('gameplay', 'visual'):
        return 'source_fit_blur', 'Landscape gameplay/visual source: preserving full HUD & action width over blurred extension.', None
    if has_center_face:
        return 'fullscreen_smart_crop', 'Central speaker face detected; using scene-aware smart crop.', None
    return 'source_fit_blur', 'Default conservative source-fit over blurred extension to preserve full frame.', None


def safe_hook_and_caption_placement(
    layout: LayoutKind,
    canvas_h: int = 1920,
    primary_y: int = 0,
    primary_h: int = 1920,
    secondary_position: str = 'bottom',
    hook_position: Literal['top', 'center', 'bottom'] = 'top',
    requested_caption_margin: float | None = None,
) -> tuple[float, float]:
    """Compute non-colliding (hook_anchor_y_px, caption_margin_frac) inside mobile safe areas.

    Adapts MIT OpenShorts hook_placement.py band-avoidance principle:
    - Keeps hook clear of top status UI (>= 7% canvas_h) and bottom platform UI (<= 78% canvas_h).
    - Guarantees hook and subtitle bands do not collide.
    """
    if layout == 'gameplay_split':
        secondary_h = canvas_h - primary_h
        offset = secondary_h if secondary_position == 'top' else 0
        anchor_frac = {'top': 0.09, 'center': 0.50, 'bottom': 0.72}[hook_position]
        hook_y = offset + primary_h * anchor_frac
        margin = requested_caption_margin
        if margin is None:
            margin = min(0.68, secondary_h / canvas_h + 0.07) if secondary_position == 'bottom' else 0.16
        return round(hook_y, 2), round(margin, 4)

    if layout == 'gameplay_facecam':
        # Top 30% facecam (0..576), bottom 70% gameplay (576..1920)
        # Put hook safely in upper blur margin of gameplay panel or top safe bar without covering face
        margin = requested_caption_margin if requested_caption_margin is not None else 0.14
        hook_map = {'top': canvas_h * 0.32, 'center': canvas_h * 0.52, 'bottom': canvas_h * 0.72}
        return round(hook_map[hook_position], 2), round(margin, 4)

    if layout == 'source_fit_blur':
        # 16:9 picture centered inside 1080x1920 occupies roughly y=656..1264 (34%..66%)
        # Place top hook cleanly in the upper blurred band (y ~ 15% = 288px) and captions in lower blurred band (margin ~ 20%)
        margin = requested_caption_margin if requested_caption_margin is not None else 0.20
        hook_map = {'top': canvas_h * 0.15, 'center': canvas_h * 0.30, 'bottom': canvas_h * 0.69}
        return round(hook_map[hook_position], 2), round(margin, 4)

    # Full-screen smart crop / two-speaker split / center crop
    margin = requested_caption_margin if requested_caption_margin is not None else (0.46 if layout == 'two_speaker_split' else 0.16)
    hook_map = {'top': canvas_h * 0.11, 'center': canvas_h * 0.45, 'bottom': canvas_h * 0.70}
    hook_y = hook_map[hook_position]
    # Avoid caption collision if hook is near caption band
    caption_top_px = canvas_h * (1.0 - margin - 0.10)
    if abs(hook_y - caption_top_px) < canvas_h * 0.08:
        hook_y = max(canvas_h * 0.10, caption_top_px - canvas_h * 0.12)
    return round(hook_y, 2), round(margin, 4)


def build_edit_manifest(
    source_start: float,
    source_end: float,
    request: Any,
    source_width: int = 1280,
    source_height: int = 720,
    facecam_corners: list[str] | None = None,
    has_center_face: bool = False,
    two_speakers_detected: bool = False,
    internal_cuts: list[float] | None = None,
    climax_time: float | None = None,
    design_preset: DesignPresetName | None = None,
    has_explicit_background: bool = False,
) -> EditManifest:
    """Create one deterministic, schema-validated EditManifest for preview and rendering."""
    duration = max(0.1, float(source_end - source_start))
    mode = getattr(request, 'mode', 'shorts')
    out_w, out_h = (1080, 1920) if mode == 'shorts' else (getattr(request, 'output_height', 720) * 16 // 9 // 2 * 2, getattr(request, 'output_height', 720))

    preset_cfg = DESIGN_PRESETS.get(design_preset, {}) if design_preset else {}
    req_preset = getattr(request, 'preset', 'auto')
    category = getattr(request, 'source_category', 'auto')

    layout, reason, fc_corner = choose_smart_layout(
        source_width=source_width,
        source_height=source_height,
        source_category=category,
        requested_preset=req_preset,
        has_explicit_background=has_explicit_background,
        facecam_corners=facecam_corners,
        has_center_face=has_center_face,
        two_speakers_detected=two_speakers_detected,
        mode=mode,
    )

    split_ratio = float(getattr(request, 'split_ratio', 0.65))
    sec_pos = getattr(request, 'background_position', 'bottom')
    primary = PanelBounds(x=0, y=0, width=out_w, height=out_h)
    secondary = None

    if layout == 'gameplay_split':
        prim_h = 2 * round(out_h * split_ratio / 2)
        sec_h = out_h - prim_h
        offset = sec_h if sec_pos == 'top' else 0
        primary = PanelBounds(x=0, y=offset, width=out_w, height=prim_h)
        secondary = PanelBounds(x=0, y=0 if offset else prim_h, width=out_w, height=sec_h)
    elif layout == 'gameplay_facecam':
        cam_h = 2 * round(out_h * 0.30 / 2)  # 576px top facecam
        game_h = out_h - cam_h               # 1344px bottom gameplay
        secondary = PanelBounds(x=0, y=0, width=out_w, height=cam_h)
        primary = PanelBounds(x=0, y=cam_h, width=out_w, height=game_h)
    elif layout == 'two_speaker_split':
        half = (out_h // 2) // 2 * 2
        primary = PanelBounds(x=0, y=0, width=out_w, height=half)
        secondary = PanelBounds(x=0, y=half, width=out_w, height=out_h - half)

    hook_pos = getattr(request, 'hook_position', None) or preset_cfg.get('hook_position', 'top')
    req_margin = getattr(request, 'caption_margin', None)
    hook_y, cap_margin = safe_hook_and_caption_placement(
        layout=layout,
        canvas_h=out_h,
        primary_y=primary.y,
        primary_h=primary.height,
        secondary_position=sec_pos,
        hook_position=hook_pos,
        requested_caption_margin=req_margin,
    )

    hook_mode = getattr(request, 'hook_mode', None)
    text_mode = getattr(request, 'text_mode', None)
    if hook_mode is None:
        hook_mode = 'persistent' if text_mode in ('static_hook', 'hook_subtitles') else ('none' if not getattr(request, 'hook', '') else 'intro')

    effects: list[MotionEffect] = []
    # Subtle, editorially justified punch-in ONLY on verified climax when captions won't be cropped and preset allows
    if (
        preset_cfg.get('allow_punch_in')
        and climax_time is not None
        and layout in ('source_fit_blur',)
        and source_start + 2.0 < climax_time < source_end - 2.0
    ):
        rel = climax_time - source_start
        effects.append(MotionEffect(
            type='punch_in',
            start=round(max(0.0, rel - 0.5), 2),
            end=round(min(duration, rel + 2.5), 2),
            strength=0.06,
            reason='Subtle emphasis on verified climax moment',
        ))

    return EditManifest(
        source_start=round(source_start, 3),
        source_end=round(source_end, 3),
        duration=round(duration, 3),
        internal_cuts=[round(c - source_start, 3) for c in (internal_cuts or []) if source_start < c < source_end],
        output_width=out_w,
        output_height=out_h,
        layout=layout,
        layout_reason=reason,
        design_preset=design_preset,
        primary_panel=primary,
        secondary_panel=secondary,
        facecam_corner=fc_corner,  # type: ignore[arg-type]
        secondary_asset=getattr(request, 'background', None) if layout == 'gameplay_split' else None,
        split_crop=getattr(request, 'split_crop', 'blurred'),
        hook_text=getattr(request, 'hook', '') or '',
        hook_mode=hook_mode,
        hook_position=hook_pos,
        hook_anchor_y=hook_y,
        hook_font=getattr(request, 'hook_font', None) or preset_cfg.get('hook_font', 'sans'),
        hook_font_size=int(getattr(request, 'hook_font_size', None) or preset_cfg.get('hook_font_size', 54)),
        hook_color=getattr(request, 'hook_color', None) or preset_cfg.get('hook_color', '#FFFFFF'),
        hook_outline=int(getattr(request, 'hook_outline', 3)),
        hook_shadow=bool(getattr(request, 'hook_shadow', True)),
        hook_box=bool(getattr(request, 'hook_box', False)),
        captions_enabled=bool(getattr(request, 'captions', True)),
        caption_style=getattr(request, 'caption_style', None) or preset_cfg.get('caption_style', 'pop'),
        caption_margin=cap_margin,
        audio_mode=getattr(request, 'audio_mode', 'original'),
        secondary_audio_muted=True,
        motion_effects=effects,
        provenance={'source_dimensions': [source_width, source_height], 'source_category': category},
    )


def facecam_crop_filter(corner: str, out_w: int, cam_h: int) -> str:
    """Build deterministic FFmpeg crop+scale filter for a detected corner facecam."""
    # Typical streamer facecam covers ~25-30% width x ~30-35% height in a corner
    x_expr = '0' if corner.endswith('left') else 'iw-iw*0.28'
    y_expr = '0' if corner.startswith('top') else 'ih-ih*0.34'
    return (
        f"crop=w=trunc(iw*0.28/2)*2:h=trunc(ih*0.34/2)*2:x={x_expr}:y={y_expr},"
        f"scale={out_w}:{cam_h}:force_original_aspect_ratio=increase,crop={out_w}:{cam_h},setsar=1"
    )
