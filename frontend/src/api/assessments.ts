import{getJson,isRecord}from'./client';import type{Assessment,AssessmentList,Health}from'./types';
export const isAssessment=(v:unknown):v is Assessment=>isRecord(v)&&typeof v.assessment_id==='string'&&typeof v.name==='string'&&['DRAFT','ACTIVE','SEALED'].includes(String(v.status));
const isList=(v:unknown):v is AssessmentList=>isRecord(v)&&typeof v.total==='number'&&Array.isArray(v.items)&&v.items.every(isAssessment);
const isHealth=(v:unknown):v is Health=>isRecord(v)&&(v.status==='ok'||v.status==='degraded')&&typeof v.audit_chain_valid==='boolean';
export const fetchAssessments=()=>getJson('/api/assessments?limit=100&offset=0',isList);
export const fetchCurrent=()=>getJson('/api/assessments/current',isAssessment);
export const fetchHealth=()=>getJson('/health',isHealth);
