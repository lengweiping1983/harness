"""本机 Superagent 工作台的协议源，与实际路由和查询字段共同维护。"""
from __future__ import annotations
from ..app.superagent_queries import QUERY_RESOURCES

TEXT = {'type':'string'}
RECORD = {'type':'object','additionalProperties':True}


def obj(properties,required=None):
    return {'type':'object','properties':properties,'required':list(properties) if required is None else required,'additionalProperties':False}


READ_RESOURCES = (*QUERY_RESOURCES,'mission','policy','status','receipt','effect','local_options')
READ_QUERY = {'type':'object','additionalProperties':False,'required':['resource'],'properties':{
    'resource':{'type':'string','enum':list(READ_RESOURCES)},'mission_id':{'type':'string','minLength':1,'maxLength':256},
    'limit':{'type':'integer','minimum':1,'maximum':100,'default':32},
    'cursor':{'type':'string','minLength':1,'maxLength':2048},'identifier':{'type':'string','minLength':1,'maxLength':256}}}
SCHEMAS = {
    'SuperagentConnection':obj({'connection_id':TEXT,'instance_id':TEXT,'config_path':TEXT,
        'runtime':{'type':'string','enum':['running','stopped','failed','unavailable']},'error_code':{'type':['string','null']}}),
    'SuperagentConnections':obj({'connections':{'type':'array','items':{'$ref':'#/components/schemas/SuperagentConnection'}}}),
    'SuperagentConnect':obj({'config_path':{'type':'string','minLength':1,'maxLength':4096}}),
    'SuperagentRead':obj({'connection_id':TEXT,'instance_id':TEXT,'resource':TEXT,'data':RECORD}),
    'SuperagentCommand':obj({'request_id':{'type':'string','minLength':1,'maxLength':96},
        'action':{'type':'string','enum':['start','stop','submit','control','steer','source','policy','requeue','verify','media','reply','answer']},'payload':RECORD}),
    'SuperagentWrite':obj({'connection_id':TEXT,'instance_id':TEXT,'request_id':TEXT,'receipt':RECORD}),
}
PREFIX = '/api/v1/superagents'
ENDPOINTS = {
    ('GET',PREFIX):(None,'SuperagentConnections'),
    ('POST',PREFIX+'/connect'):('SuperagentConnect','SuperagentConnection'),
    ('GET',PREFIX+'/{connection_id}'):(None,'SuperagentConnection'),
    ('GET',PREFIX+'/{connection_id}/read'):(None,'SuperagentRead'),
    ('POST',PREFIX+'/{connection_id}/commands'):('SuperagentCommand','SuperagentWrite'),
}
