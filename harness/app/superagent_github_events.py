"""GitHub Review／CI 和 PR 进度事件只提供来源上下文，不授予执行权限。"""
from __future__ import annotations

import re


def activity(data,kind,action,delivery,config,received_at):
    from .superagent_coordinator_store import _hash,_json
    from .superagent_source_registry import (
        _bounded_text as text,_positive_id as positive,_timestamp,
        NormalizedTrigger,NormalizedThread,SourceIgnored,
    )
    supported = {'pull_request_review':{'submitted','edited','dismissed'},
        'pull_request_review_comment':{'created','edited','deleted'},
        'pull_request':{'synchronize','ready_for_review','converted_to_draft','edited','labeled','unlabeled'},
        'check_run':{'created','completed','rerequested'},'check_suite':{'completed','requested','rerequested'},
        'workflow_run':{'requested','in_progress','completed'}}
    if action not in supported.get(kind,set()):
        return SourceIgnored('unsupported_github_activity')
    repository = data['repository']
    if positive(repository['id'],'repository.id')!=config['repository_id'] or text(repository['full_name'],'repository').casefold()!=config['repository'].casefold():
        raise PermissionError('github/repository')
    sender = data['sender']
    sender_id = positive(sender['id'],'sender.id')
    login = text(sender['login'],'sender.login',limit=100)
    if sender.get('type') not in {'User','Bot'}:
        raise ValueError('github/sender')
    ci = kind in {'check_run','check_suite','workflow_run'}
    if ci:
        entity = data[kind]
        pulls = entity.get('pull_requests',[])
        if not isinstance(pulls,list) or len(pulls)!=1:
            return SourceIgnored('ambiguous_github_check_target')
        pull = pulls[0]
        head_sha = text(entity['head_sha'],'head_sha',limit=40)
        if (not re.fullmatch(r'[0-9a-f]{40}',head_sha) or pull.get('head',{}).get('sha')!=head_sha
                or positive(pull.get('base',{}).get('repo',{}).get('id'),'pull.base.repo.id')!=config['repository_id']):
            raise PermissionError('github/check-scope')
        body = {'text':'','head_sha':head_sha,'check':{'kind':kind,'id':str(positive(entity['id'],'check.id')),
            'status':text(entity.get('status',''),'status',limit=80,empty=True),
            'conclusion':text(entity.get('conclusion') or '','conclusion',limit=80,empty=True)}}
    else:
        pull = data['pull_request']
        if positive(pull['base']['repo']['id'],'pull.base.repo.id')!=config['repository_id']:
            raise PermissionError('github/pull-scope')
        head_sha = text(pull['head']['sha'],'head_sha',limit=40)
        if not re.fullmatch(r'[0-9a-f]{40}',head_sha):
            raise ValueError('github/head-sha')
        entity = data.get('review') or data.get('comment') or pull
        body = {'text':text(entity.get('body') or '','body',limit=65536,empty=True),'head_sha':head_sha}
        if kind in {'pull_request_review','pull_request_review_comment'} and action not in {'dismissed','deleted'}:
            if positive(entity['user']['id'],'author.id')!=sender_id:
                raise PermissionError('github/activity-author')
        if kind=='pull_request_review':
            body['review'] = {'id':str(positive(entity['id'],'review.id')),
                'state':text(entity['state'],'review.state',limit=80),'commit_id':text(entity['commit_id'],'commit_id',limit=40)}
            if not re.fullmatch(r'[0-9a-f]{40}',body['review']['commit_id']):
                raise ValueError('github/review-commit')
            body['review']['outdated'] = body['review']['commit_id']!=head_sha
        elif kind=='pull_request':
            labels = pull.get('labels',[])
            if not isinstance(labels,list) or len(labels)>100:
                raise ValueError('github/labels')
            body['pull'] = {'draft':bool(pull.get('draft',False)),'state':text(pull.get('state',''),'pull.state',limit=40,empty=True),
                'labels':[text(label['name'],'label.name',limit=256) for label in labels]}
            if action in {'labeled','unlabeled'}:
                body['pull']['changed_label'] = text(data['label']['name'],'label.name',limit=256)
        elif kind=='pull_request_review_comment':
            body['comment'] = {'id':str(positive(entity['id'],'comment.id')),
                'path':text(entity.get('path',''),'path',limit=4096,empty=True)}
    bot = ci or sender['type']=='Bot' or sender_id==config['bot_user_id']
    number = positive(pull['number'],'pull.number')
    thread = 'issue/'+str(number)
    revision = _hash(_json([body,entity.get('updated_at') or entity.get('submitted_at') or entity.get('completed_at')]))
    return NormalizedTrigger(external_event_id='github:'+kind+':'+str(positive(entity['id'],'entity.id'))+':'+action+':'+revision,
        event_kind='github.'+kind+'.'+action,actor_id='github:'+('bot' if bot else 'user')+':'+str(sender_id),
        actor_kind='bot' if bot else 'human',
        occurred_at=_timestamp(entity.get('updated_at') or entity.get('submitted_at') or entity.get('completed_at') or entity.get('created_at'),received_at),
        addressed='none',threads=(NormalizedThread(thread,'primary',{'repository':repository['full_name']}),),
        primary_thread_id=thread,body=body,metadata={'repository':config['repository'],'repository_id':config['repository_id'],
            'thread_number':number,'sender_login':login,'delivery_id':delivery,'scope_row':'default','context_event':True},
        channel_address={'repository':config['repository'],'issue_number':str(number),'thread_id':thread})
