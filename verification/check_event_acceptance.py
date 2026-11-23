"""Offline full-source GTA acceptance with existing ASR; no publishing."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import av

from factory import config, media, metadata, visual_analysis
from factory.models import JobRequest
from factory.shorts import plan
from jsonfile import write_json_atomic


def main(source, transcript_path, background, output):
    output.mkdir(parents=True,exist_ok=True)
    transcript=json.loads(transcript_path.read_text(encoding='utf-8'))
    with av.open(str(source)) as container:
        decoded_duration=container.duration/av.time_base
    if abs(decoded_duration-618)>1:
        raise ValueError(f'Full 618-second GTA source required; received {decoded_duration:.3f}s')
    duration=min(618,decoded_duration)
    request=JobRequest(source='upload:gta-full.mp4',rights_confirmed=True,count=3,minimum=35,maximum=60,
        end=duration,source_category='gameplay',selection_version=2,preset='gameplay',split_ratio=.65,
        split_crop='blurred',background_mode='specific',background=background.name,text_mode='hook_subtitles',
        hook='GTA GAMEPLAY',hook_mode='persistent',hook_font_size=60,hook_box=False,confirmed_subject='GTA',
        metadata_language='en',save_drive=False)
    spec={'title':'GTA 6 FUNNY MOMENTS & WINS #1 (GTA 6 FAILS)','platform':'YouTube'}
    url='https://www.youtube.com/watch?v=yux8s3lwLss'
    start=time.monotonic()
    directory=config.SCRATCH/'uploads'
    directory.mkdir(parents=True,exist_ok=True)
    link=directory/'gta-full.mp4'
    if not link.exists():link.symlink_to(source.resolve())
    features=visual_analysis.sample(request.source,0,duration,output,lambda *_:None)
    after_coarse=time.monotonic()
    independent=plan(transcript,request,duration,features,lambda *_:None)
    independent=visual_analysis.refine_local_plan(source,0,request,independent,transcript,duration)
    before_connected=time.monotonic()
    connected=plan(transcript,request.model_copy(update={'series_mode':'connected'}),duration,features,lambda *_:None)
    after_connected=time.monotonic()
    sequential=plan(transcript,request.model_copy(update={'series_mode':'sequential'}),150,[],lambda *_:None)
    comparison=[]
    for clip in independent['candidates']:
        a,b=clip['start_seconds'],clip['end_seconds']
        cuts=[a,*[cut for cut in clip.get('fine_review',{}).get('possible_cuts',[]) if a<cut<b],b]
        longest=max(y-x for x,y in zip(cuts,cuts[1:]))
        comparison.append({'interval':[a,b],'old_longest_shot':longest,'old_fine_rejected_if_visual':longest<35,
                           'new_status':'editorial_review_required'})
    with source.open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    record={'source':{'url':url,'analyzed_duration':duration,'media_duration':decoded_duration,
                      'full_media':True,'sha256':digest},
            'elapsed_seconds':{'coarse':after_coarse-start,'independent':before_connected-after_coarse,
                               'connected':after_connected-before_connected},
            'timeline':[s for s in features if s['cut']], 'feature_count':len(features),
            'independent':independent,'connected':connected,'sequential':sequential,
            'old_new_comparison':comparison,'outputs':[],'editorial_quality':'unverified'}
    write_json_atomic(output/'report.json',record)
    print(json.dumps({'source':record['source'],'counts':[len(independent['candidates']),len(connected['candidates']),
                     len(sequential['candidates'])],'discovered':independent['discovered_candidates'],
                     'connected_summary':connected['selection_summary']}),flush=True)
    for mode, selection in [('independent',independent),('connected',connected)]:
        for index,clip in enumerate(selection['candidates'],1):
            a,b=clip['start_seconds'],clip['end_seconds']
            file=output/f'{mode}-{index}.mp4'
            raw=config.SCRATCH/f'{mode}-{index}-raw.mp4'
            try:
                media.download(request.source,raw,a,b,360)
                media.render(raw,file,transcript,a,b,request,background)
            except (ValueError,RuntimeError,OSError,TimeoutError) as error:
                record['outputs'].append({'mode':mode,'interval':[a,b],'error':str(error)})
                write_json_atomic(output/'report.json',record)
                print(file.name,'failed:',str(error),flush=True)
                continue
            validation=json.loads(file.with_suffix('.validation.json').read_text(encoding='utf-8'))
            draft=metadata.Generator(None,None,None).create(metadata.context(
                transcript,request,spec,[clip],{'duration':validation['duration'],'validation':validation},url))
            previews=[]
            for number, n in enumerate((.12,.5,(b-a-.1)/(b-a))):
                preview=file.with_name(f'{file.stem}-frame-{number}.jpg')
                media.run([media.ffmpeg(),'-loglevel','error','-y','-threads','1','-ss',str((b-a)*n),
                    '-i',str(file),'-frames:v','1','-vf','scale=360:-2',str(preview)])
                previews.append(preview.name)
            record['outputs'].append({'mode':mode,'interval':[a,b],'file':file.name,'frames':previews,
                                      'validation':validation,'metadata':draft})
            write_json_atomic(output/'report.json',record)
            print(file.name,a,b,validation['duration'],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','transcript','background','output'):
        parser.add_argument(name,type=Path)
    args=parser.parse_args()
    main(args.source,args.transcript,args.background,args.output)
