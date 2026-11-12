"""Bounded structural diagnostics; never copy model text, args or exception text."""
import json

REASONS={
    'Invalid final JSON':'FINAL_JSON_INVALID',
    'Final fields':'FINAL_FIELDS_INVALID',
    'Invalid final output':'FINAL_VALUE_OR_EVIDENCE_INVALID',
    'Visibility changed':'FINAL_VISIBILITY_CHANGED',
    'Final movie violates server-enforced conditions':'FINAL_CONDITION_CONFLICT',
    'Unresolved explicit user conditions':'CLARIFICATION_REQUIRED',
    'Discovery limit':'DISCOVERY_LIMIT',
    'Discovery fields':'DISCOVERY_FIELDS_INVALID',
    'Discovery year':'DISCOVERY_YEAR_INVALID',
    'Invalid tool call':'TOOL_ENVELOPE_INVALID',
    'Duplicate tool call IDs':'TOOL_IDS_DUPLICATED',
    'Terminal tool must be alone':'TERMINAL_TOOL_BATCHED',
    'Structured intent required before final':'INTENT_MISSING',
    'Plan answer requires no actions':'PLAN_ANSWER_WITH_ACTIONS',
}

def failure_details(stage,error,response=None):
    result={'stage':stage,'reason':REASONS.get(str(error),'INVALID_JSON' if isinstance(error,json.JSONDecodeError) else 'VALIDATION_FAILED'),
            'exception_type':type(error).__name__ if type(error) in (ValueError,TypeError,KeyError,json.JSONDecodeError) else 'OTHER'}
    if not isinstance(response,dict):return result
    result['has_tool_calls']=bool(response.get('tool_calls'))
    content=response.get('content')
    result['content_type']=type(content).__name__ if type(content) in (str,dict,list,type(None)) else 'OTHER'
    try:body=json.loads(content) if type(content) is str else None
    except (ValueError,TypeError,RecursionError):result['content_json']='INVALID';return result
    result['content_json']='OBJECT' if type(body) is dict else 'NON_OBJECT'
    if type(body) is dict:
        result['final_fields_present']=sorted(set(body)&{'answer','movie_ids','discoveries'})
        result['extra_field_count']=len(set(body)-{'answer','movie_ids','discoveries'})
        result['field_types']={k:type(body[k]).__name__ if type(body[k]) in (str,int,bool,list,dict,type(None)) else 'OTHER' for k in result['final_fields_present']}
    return result
