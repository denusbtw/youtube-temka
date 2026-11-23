"""Validated layout geometry shared by the browser preview and FFmpeg."""
from typing import Literal
from pydantic import BaseModel,Field


class GeometryRequest(BaseModel):
    ratio: float = Field(default=.65,ge=.2,le=.8,allow_inf_nan=False)
    secondary_position: Literal['top','bottom'] = 'bottom'
    split: bool = False
    hook_position: Literal['top','center','bottom'] = 'top'
    caption_margin: float | None = Field(default=None,ge=.07,le=.75,allow_inf_nan=False)


def geometry(value):
    height=1920
    primary=2*round(height*value.ratio/2) if value.split else height
    secondary=height-primary
    offset=secondary if value.secondary_position=='top' else 0
    anchor={'top':.09,'center':.5,'bottom':.72}[value.hook_position]
    margin=value.caption_margin
    if margin is None:
        margin=min(.68,secondary/height+.07) if value.split and value.secondary_position=='bottom' else .15
    return {'schema_version':1,'canvas':[1080,height],
            'primary':{'y':offset,'height':primary},'secondary':{'y':0 if offset else primary,'height':secondary},
            'hook_anchor_y':offset+primary*anchor,'hook_position':value.hook_position,'caption_margin':margin}
