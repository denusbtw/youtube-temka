from typing import Literal
from pydantic import BaseModel, Field, model_validator
from . import config


class CompilationSource(BaseModel):
    source: str = Field(min_length=1, max_length=2048)
    start: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, le=86400, allow_inf_nan=False)

    @model_validator(mode='after')
    def validate_interval(self):
        if self.end is not None and self.end <= self.start:
            raise ValueError('Source end must be after its start')
        return self


class JobRequest(BaseModel):
    source: str = Field(min_length=1, max_length=2048)
    rights_confirmed: bool
    workflow_version: Literal[1, 2] = 1
    story_mode: Literal['chronological', 'ai_verified'] | None = None
    workflow: Literal['highlights', 'story', 'compilation'] | None = None
    sources: list[CompilationSource] = Field(default_factory=list, max_length=12)
    story_count: int | None = Field(default=None, ge=1, le=100)
    destination: Literal['youtube_shorts', 'video'] | None = None
    review_before_render: bool = False
    transition: Literal['cut','fade'] = 'cut'
    mode: Literal['shorts','longs'] = 'shorts'
    resolution: Literal[480,720,1080,'best'] = 720
    output_height: Literal[720,1080] = 720
    bitrate: int = Field(default=8000000,ge=1000000,le=50000000)
    caption_style: Literal['pop','karaoke','clean'] = 'pop'
    caption_margin: float | None = Field(default=None,ge=.07,le=.75,allow_inf_nan=False)
    split_ratio: float = Field(default=.65,ge=.2,le=.8,allow_inf_nan=False)
    hook: str = Field(default='',max_length=120)
    text_mode: Literal['none','subtitles','static_hook','hook_subtitles','custom'] | None = None
    hook_mode: Literal['persistent','timed','intro','none'] | None = None
    hook_start: float = Field(default=0,ge=0,le=3600,allow_inf_nan=False)
    hook_end: float = Field(default=15,gt=0,le=3600,allow_inf_nan=False)
    hook_duration: float = Field(default=3,ge=.5,le=30,allow_inf_nan=False)
    hook_position: Literal['top','center','bottom'] = 'top'
    hook_font_size: int = Field(default=48,ge=18,le=96)
    hook_font: Literal['sans','serif','mono'] = 'sans'
    hook_color: str = Field(default='#FFFFFF',pattern=r'^#[0-9A-Fa-f]{6}$')
    hook_outline: int = Field(default=2,ge=0,le=8)
    hook_shadow: bool = True
    hook_box: bool = True
    subtitle_language: str | None = Field(default=None,pattern=r'^(en|es|pt-BR|de|ru|uk)$')
    series_mode: Literal['independent','connected','sequential'] = 'independent'
    selection_version: Literal[2] | None = None
    plan_only: bool = False
    experiment_parent: str | None = Field(default=None,pattern=r'^[0-9a-f]{32}:\d{1,2}$')
    background_position: Literal['top','bottom'] = 'bottom'
    background_speed: float = Field(default=1,ge=.5,le=2,allow_inf_nan=False)
    background_offset: float = Field(default=0,ge=0,le=3600,allow_inf_nan=False)
    background_loop: bool = True
    split_crop: Literal['fill','fit','blurred'] = 'blurred'
    series_overlap: float = Field(default=3,ge=0,le=5,allow_inf_nan=False)
    target_duration: float | None = Field(default=None,ge=10,le=3600,allow_inf_nan=False)
    logo_asset: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9_-]+\.png$')
    cta_text: str = Field(default='',max_length=120)
    background_mode: Literal['auto','none','specific','random'] = 'auto'
    background_selection_reason: str | None = Field(default=None,max_length=300)
    background_category: str | None = Field(default=None,max_length=80)
    preserve_fps: bool = False
    asr_model: Literal['tiny','base','small','medium','large-v3','large-v3-turbo'] | None = None
    llm_model: str | None = Field(default=None,pattern=r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$')
    start: float = Field(default=0, ge=0, allow_inf_nan=False)
    end: float | None = Field(default=1800, gt=0, le=86400, allow_inf_nan=False)
    count: int = Field(default=5, ge=1, le=20)
    minimum: float = Field(default=20, ge=5, le=600, allow_inf_nan=False)
    maximum: float = Field(default=60, ge=5, le=600, allow_inf_nan=False)
    preset: Literal['auto','smart','crop','blur','gameplay'] = 'auto'
    audio_mode: Literal['original','music','silent','voiceover'] = 'original'
    music_asset: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9_-]+\.(mp3|wav|mp4)$')
    tts_voice: Literal['af_heart','af_bella','am_michael','ef_dora','em_alex'] = 'af_heart'
    tts_text: str = Field(default='',max_length=5000)
    tts_speed: float = Field(default=1,ge=.85,le=1.15,allow_inf_nan=False)
    silence_removal: bool = False
    confirmed_subject: str = Field(default='',max_length=100)
    clip_observation: str = Field(default='',max_length=500)
    include_source_link: bool = False
    attribution_required: bool = False
    category_override: str | None = Field(default=None,pattern=r'^\d{1,3}$')
    source_attribution: str = Field(default='',max_length=1000)
    source_category: Literal['auto','podcast','stream','gameplay','visual'] = 'auto'
    background: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_-]+\.mp4$')
    language: str | None = Field(default=None, pattern=r'^[a-z]{2,3}$')
    metadata_language: str | None = Field(default=None,pattern=r'^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$')
    metadata_preferences: dict = Field(default_factory=dict)
    captions: bool = True
    save_drive: bool = True
    publish_channel: str | None = Field(default=None,pattern=r'^[A-Za-z0-9_-]{1,100}$')
    youtube_channel_key: Literal['leonida_mayhem', 'thevicescope'] | None = None
    publish_automatic: bool = False
    intervals: list[tuple[float,float]] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def validate_job(self):
        if not self.rights_confirmed:
            raise ValueError('Confirm rights to the source and selected background')
        if self.workflow == 'compilation':
            if len(self.sources) < 2 or self.sources[0].source != self.source:
                raise ValueError('Provide at least two sources with the first as the primary source')
            if len({(s.source, s.start, s.end) for s in self.sources}) != len(self.sources):
                raise ValueError('Remove duplicate source ranges from the compilation')
            if self.intervals or self.destination == 'youtube_shorts':
                raise ValueError('Compilations use per-source ranges and regular video output')
            if self.audio_mode == 'voiceover':
                raise ValueError('Compilation supports original sound, music, or silent audio')
            self.mode = 'longs'
            self.preserve_fps = False
        elif self.sources:
            raise ValueError('Multiple sources require Longs Compilation')
        if self.workflow == 'story':
            if self.workflow_version == 2 and self.story_mode is None:
                raise ValueError('Choose chronological or AI-verified story mode')
            self.story_mode = self.story_mode or 'chronological'
            if self.intervals or self.silence_removal:
                raise ValueError('Story parts cover the full range; remove manual clips and pause removal')
            if self.review_before_render:
                raise ValueError('Story boundaries are constrained by complete source coverage')
            self.mode = 'shorts'
            self.series_mode = 'sequential' if self.story_mode == 'chronological' else 'connected'
            if self.story_mode == 'ai_verified':
                self.selection_version = 2
                if self.story_count is not None:
                    if self.story_count > 20:
                        raise ValueError('AI-verified stories support up to 20 parts')
                    self.count = self.story_count
        elif self.workflow:
            self.series_mode = 'independent'
            if self.workflow == 'highlights' and self.workflow_version == 2 and self.mode == 'shorts':
                self.selection_version = 2
        if self.destination == 'youtube_shorts' and (
            self.mode != 'shorts' or self.maximum > config.SHORTS_MAX_SECONDS
            or any(b-a > config.SHORTS_MAX_SECONDS for a,b in self.intervals)
        ):
            raise ValueError(f'YouTube Shorts require vertical output and at most {config.SHORTS_MAX_SECONDS} seconds per part; lower the maximum or select regular video')
        if self.text_mode is not None:
            self.captions=self.text_mode in ('subtitles','hook_subtitles')
            if self.text_mode in ('none','subtitles'):self.hook=''
        if self.attribution_required and not self.source_attribution.strip():
            raise ValueError('Documented attribution text is required')
        if self.hook_mode=='timed' and self.hook_end<=self.hook_start:
            raise ValueError('Hook end must follow hook start')
        if self.text_mode=='custom' and not self.hook.strip():raise ValueError('Custom Text requires nonempty text')
        if self.series_mode!='independent' and self.mode!='shorts':raise ValueError('Series modes require Shorts')
        if self.plan_only and (self.mode!='shorts' or self.publish_automatic):raise ValueError('Planning requires Shorts with automatic publishing disabled')
        if self.series_mode=='sequential' and self.intervals:raise ValueError('Sequential Source Split uses the selected start and end')
        if self.series_overlap>=self.minimum:raise ValueError('Series overlap must be shorter than the minimum clip')
        if self.review_before_render and (self.plan_only or self.publish_automatic):
            raise ValueError('Edit-plan review requires automatic publishing and planning-only mode to be disabled')
        if self.workflow == 'compilation' and self.silence_removal:
            raise ValueError('Compilation preserves segment timing and cannot remove silence')
        if self.publish_automatic and (not self.save_drive or not self.publish_channel):
            raise ValueError('Automatic publishing requires a selected authorized channel and verified Drive storage')
        if (self.end is not None and self.end <= self.start) or self.minimum > self.maximum:
            raise ValueError('Invalid interval or clip duration bounds')
        for a,b in self.intervals:
            import math
            if not math.isfinite(a+b) or not self.start <= a < b <= (self.end or 86400):
                raise ValueError('Longs intervals must lie inside the processing interval')
        if self.audio_mode=='voiceover' and self.language and self.language not in ('en','es'):
            raise ValueError('Licensed Kokoro voices currently support English/Spanish here. Use original audio for Ukrainian/Russian.')
        return self


class Candidate(BaseModel):
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(gt=0, allow_inf_nan=False)
    score: float = Field(ge=0, le=100, allow_inf_nan=False)
    title: str = Field(min_length=1,max_length=150)
    language: str = Field(max_length=10)
    reason: str = Field(max_length=1000)
    confidence: float = Field(ge=0,le=1, allow_inf_nan=False)


class Ranking(BaseModel):
    candidates: list[Candidate] = Field(max_length=30)
    decision: Literal['highlights','unsuitable','visual_required'] | None = None
    explanation: str = Field(default='',max_length=500)
