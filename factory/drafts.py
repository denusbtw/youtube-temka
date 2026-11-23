"""Controller-side draft backfill/revisions, independent of YouTube consent."""
from datetime import datetime,timezone
import json
import uuid
from . import config,store,media,metadata
from .models import JobRequest


def contexts_for_job(job):
    request=JobRequest.model_validate(job['request'])
    if request.publish_channel:request.metadata_preferences=store.preferences(request.publish_channel)
    path=config.SCRATCH/job['id']/'transcript.json';temporary=False
    history=store.metadata_history(job['id'],0)
    if history and history[0]['provenance'].get('input'):
        # Actual transcript evidence remains available after cloud/local cleanup.
        saved=[]
        for index in range(len(job['result'].get('outputs',[]))):
            revisions=store.metadata_history(job['id'],index)
            if not revisions or not revisions[0]['provenance'].get('input'):break
            ctx=revisions[0]['provenance']['input'];ctx['preferences']=request.metadata_preferences
            ctx['brief']=metadata.brief(request,ctx.get('source_title',''))
            if request.metadata_language:ctx['metadata_language']=request.metadata_language
            saved.append(ctx)
        if len(saved)==len(job['result'].get('outputs',[])):return saved
    if not path.is_file():
        remote=job['result'].get('artifacts',{}).get('transcript.json')
        if remote:
            if int(remote.get('size',0))>16*1024**2:raise ValueError('Transcript exceeds the bounded draft-backfill limit')
            path=config.SCRATCH/'draft-cache'/job['id']/'transcript.json';path.parent.mkdir(parents=True,exist_ok=True);temporary=True
            media.fetch_drive(remote,path)
    try:
        transcript=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'language':job['result'].get('language'),'segments':[]}
        candidates=job['result'].get('candidates',[])
        return [metadata.context(transcript,request,job['result'].get('source',{}),candidates if request.mode=='longs' else candidates[i:i+1],o,job['result'].get('source_url') or (request.source if request.source.startswith('https://') else None)) for i,o in enumerate(job['result'].get('outputs',[]))]
    finally:
        if temporary:path.unlink(missing_ok=True)


def regenerate(jid, force_overwrite=False):
    job=store.get(jid)
    if not job or job['state']!='READY':raise ValueError('Metadata generation is available for completed jobs')
    contexts=contexts_for_job(job)
    drafts=metadata.generate_outputs(contexts,job['request'].get('llm_model'))
    for index,value in enumerate(drafts):
        if not force_overwrite:
            history=store.metadata_history(jid,index)
            if history and history[0].get('manual'):
                # Preserve manually edited metadata unless force_overwrite is explicitly requested
                continue
        store.revise_metadata(jid,index,value,manual=False)
    return store.get(jid)


def revise(jid,index,fields):
    job=store.get(jid)
    if not job or job['state']!='READY':raise ValueError('Only completed drafts can be edited')
    original=job['result']['outputs'][index].get('publishing_metadata')
    if not original:raise ValueError('Generate publishing metadata first')
    current={k:v for k,v in original.items() if k in metadata.PublishingMetadata.model_fields}
    current.update(fields)
    credit=job['request'].get('source_attribution','')
    from .publisher import credited_description
    current['source_attribution']=credit
    current['description']=credited_description(current['description'],credit)
    current['tags']=metadata.clean_tags(current['tags'])
    if 'hashtags' in fields:
        import re
        current['description']=re.sub(r'(?<!\w)#[\w]+','',current['description']).strip()
        current['description']=credited_description(current['description'],' '.join(current['hashtags']))
    current['review_required']=True
    current['validation_issues']=['manual_revision_requires_review']
    validated=metadata.PublishingMetadata.model_validate(current)
    if validated.audio_language!=original.get('audio_language'):raise ValueError('Metadata edits cannot change the language of the actual audio')
    revisions=store.metadata_history(jid,index)
    result={**validated.model_dump(),'version':uuid.uuid4().hex,'generated_at':datetime.now(timezone.utc).isoformat(),'provenance':{**original.get('provenance',{}),'method':'manual_revision','parent_version':original['version']},'_provenance':revisions[0]['provenance'] if revisions else {}}
    store.revise_metadata(jid,index,result)
    return store.get(jid)
