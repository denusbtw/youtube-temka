"""Render owned fixtures through the Factory 9 workflows and report observations."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--youtube',action='store_true',help='Only run the authorized Blender YouTube fixture')
    args = parser.parse_args()
    parent = Path('verification/test-output')
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='factory-9-', dir=parent)).resolve()
    os.environ.update(FACTORY_DATA=str(root), FACTORY_SCRATCH=str(root/'scratch'),
                      FREE_RESERVE_GB='0', FFMPEG_ENCODER='x264')
    from factory import config, media, store
    from factory.models import JobRequest
    from factory.pipeline import process
    from factory.resources import memory_status

    if args.youtube:
        request = JobRequest(source='https://www.youtube.com/watch?v=YE7VzlLtp-4',
                             rights_confirmed=True,workflow='highlights',start=60,end=120,
                             minimum=10,maximum=60,count=1,resolution=480,preset='crop',
                             audio_mode='silent',captions=False,save_drive=False,
                             source_attribution='(c) copyright 2008, Blender Foundation / www.bigbuckbunny.org')
        job = store.create(request.model_dump())
        process(store.claim('owned-verification'))
        complete = store.get(job['id'])
        report = {'state':complete['state'],'error':complete['error'],
                  'failure':complete['result'].get('failure'),
                  'durations':[o['duration'] for o in complete['result'].get('outputs',[])]}
        (root/'youtube-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
        return 0 if complete['state']=='READY' else 1

    uploads = config.SCRATCH/'uploads'
    uploads.mkdir(parents=True)
    sources = []
    for index,(size,rate) in enumerate([('160x90',2),('192x144',3),('192x192',4)]):
        name = f'owned-shot-{index+1}.mp4'
        media.run([media.ffmpeg(),'-hide_banner','-loglevel','error','-y','-f','lavfi',
                   '-i',f'color=black:s={size}:r={rate}',
                   '-vf',"drawbox=color=white:t=fill:enable='gte(t,150)'",
                   '-t','300','-c:v','libx264','-threads','2',str(uploads/name)])
        sources.append({'source':f'upload:{name}'})

    common = {'source':sources[0]['source'],'rights_confirmed':True,'audio_mode':'silent',
              'save_drive':False,'captions':False}
    scenarios = [
        ('compilation',{'sources':sources,'minimum':300,'maximum':300,'count':3,'target_duration':900}),
        ('story',{'minimum':5,'maximum':6,'story_count':3,'series_overlap':1,'end':15,'preset':'crop'}),
    ]
    report = {'memory_at_start':memory_status(),'scenarios':[]}
    for name,options in scenarios:
        request = JobRequest(**common,workflow=name,**options)
        job = store.create(request.model_dump())
        started = time.monotonic()
        process(store.claim('owned-verification'))
        complete = store.get(job['id'])
        if complete['state'] != 'READY':
            raise RuntimeError(f'{name}: {complete["state"]}, {complete["error"]}')
        result = complete['result']
        durations = [output['validation']['duration'] for output in result['outputs']]
        if name == 'compilation':
            assert len(durations) == 1 and abs(durations[0]-900) <= 1
            timeline = json.loads((config.SCRATCH/job['id']/'timeline.json').read_text(encoding='utf-8'))
            assert len({item['source_id'] for item in timeline['segments']}) == 3
        else:
            assert len(durations) == 3 and all(5<=duration<=6.1 for duration in durations)
            assert result['candidates'][-1]['end_seconds'] == 15
        report['scenarios'].append({'workflow':name,'elapsed_seconds':round(time.monotonic()-started,2),
                                    'validated_durations':durations,'job_id':job['id']})
    report['memory_at_end'] = memory_status()
    path = root/'report.json'
    path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(path)
    print(path.read_text(encoding='utf-8'))


if __name__=='__main__':
    raise SystemExit(main())
