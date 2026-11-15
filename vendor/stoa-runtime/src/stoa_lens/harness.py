"""Bounded model tool loop, with host-owned counts and measurements."""
from __future__ import annotations

import copy
import json
import re
import time
from pathlib import Path

import jsonschema
from shapely.geometry import Polygon, box

from stoa.vision import image_content, json_content
from stoa_vista_specialists.json_response import parse_object
from stoa_vista_specialists.text.association import associate_text
from .budget import BudgetError
from .contracts import FINAL, SYSTEM, TOOLS, Limits
from .native_text import read_pdf_text
from .routing import ClassRouter


def public(value):
    excluded = {'raw', 'receipt', 'raw_events', 'stdout', 'stderr', 'effective_config', 'environment', 'path', 'pdf_path'}
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if k not in excluded}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def validation_message(exc):
    """Keep repair feedback short; full validation details remain in the journal."""
    if not isinstance(exc,jsonschema.ValidationError):return str(exc)
    path='.'.join(str(p) for p in exc.absolute_path) or 'arguments'
    if exc.validator in ('maxItems','minItems'):
        relation='At most' if exc.validator=='maxItems' else 'At least'
        rule='allowed' if exc.validator=='maxItems' else 'required'
        message=f'{relation} {exc.validator_value} items {rule}; received {len(exc.instance)}'
    elif exc.validator=='type':message=f'Expected {exc.validator_value}; received {type(exc.instance).__name__}'
    else:message=exc.message
    return path+': '+message[:600]


class LensHarness:
    def __init__(self, registry, detector, geometry, transport, directory, limits=None,
                 progress=None,conversation_context=None,cancelled=None,*,agent_profile=None,configuration=None):
        from .model_config import resolve,validate
        self.configuration=validate(configuration if configuration is not None else resolve())
        self.registry, self.detector, self.geometry, self.transport = registry, detector, geometry, transport
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.limits = limits or Limits()
        if isinstance(self.limits, dict):
            self.limits = Limits(**self.limits)
        self.router = ClassRouter()
        self.tools = copy.deepcopy(registry.archive.tools + TOOLS)
        self.agent_profile = agent_profile
        self.system_prompt = agent_profile.system if agent_profile else SYSTEM
        self.final_schema = agent_profile.final_schema if agent_profile else FINAL
        if agent_profile:
            self.tools = [t for t in self.tools if t['function']['name'] in agent_profile.tools]
        self.schemas = {t['function']['name']: t['function']['parameters'] for t in self.tools}
        self.allowances = {'detect_objects': self.limits.detector_requests,
                           'predict_room_polygons': self.limits.raster_predictions,
                           'read_pdf_text': self.limits.text_requests,
                           'read_pdf_vectors': self.limits.text_requests}
        self.uses = dict.fromkeys(self.allowances, 0)
        self.predicted_views, self.proposals, self.text_evidence = set(), {}, {}
        self.detections, self.transcript, self.measurements = [], [], []
        self.responses_used = 0
        self.failed_detector_targets = {}
        self.started = self.deadline = self.tool_deadline = None
        self.progress=progress
        self.conversation_context=conversation_context or []
        self.cancelled=cancelled

    def _boundary(self):
        if self.cancelled is not None and self.cancelled():
            from .service import Cancelled
            raise Cancelled('Cancelled after current operation; no further dispatch')

    def _emit(self, **event):
        if event.get('operation')=='response':
            if event.get('phase')=='completed':
                receipt=getattr(self.transport,'last_response',{})
                event.update(actual_model=receipt.get('actual_model'),actual_provider=receipt.get('actual_provider'))
            event.update(routing_mode=self.configuration['routing_mode'],model=self.configuration['orchestrator']['model'],
                         provider=self.configuration['orchestrator']['provider'],endpoint=self.configuration['orchestrator']['endpoint'])
        if self.progress is not None:self.progress(public(event))

    def _consume(self, name):
        if name in self.uses:
            self.uses[name] += 1
            if self.uses[name] > self.allowances[name]:
                raise ValueError('Tool allowance exceeded: ' + name)

    def tool(self, name, args):
        self._consume(name)  # Failed requests consume allowance as well.
        if time.monotonic() >= self.tool_deadline:
            raise TimeoutError('Tool phase ended; finalize with recorded evidence')
        if name not in self.schemas:
            raise ValueError('Unknown tool: ' + name)
        if self.agent_profile and name in ('native_room_candidates','predict_room_polygons','read_pdf_text'):
            page_id = args.get('page_id') if name=='native_room_candidates' else self.registry.checked_view(args['view_id'])['page_id']
            capability = {'native_room_candidates':'native_room_geometry',
                          'predict_room_polygons':'raster_room_fallback','read_pdf_text':'native_pdf_text'}[name]
            if not next(p for p in self.agent_profile.pages if p['page_id']==page_id)[capability]:
                raise ValueError('Tool unavailable for this registered page: ' + name)
        if name in ('inspect', 'read_pixels'):
            # Registry consumes view allowances before its own validation.
            try:
                jsonschema.validate(args, self.schemas[name])
            except Exception:
                self.registry.inspection_views += max(1, len(args.get('views', []))) if isinstance(args, dict) else 1
                raise
            return self.registry.execute(name, args)
        jsonschema.validate(args, self.schemas[name])
        remaining = max(.001, self.tool_deadline - time.monotonic())
        if name == 'detect_objects':
            view = self.registry.checked_view(args['view_id'])
            if self.registry.sources[view['page_id']].get('native_geometry'):
                raise ValueError('Object localization requires the source drawing, not a room-footprint preview')
            route = self.router.route(args['object_class'])
            key = (view['page_id'], route['canonical_class'])
            if key in self.failed_detector_targets:
                raise ValueError('Detector target unavailable after a failed invocation; finalize or work on another requested target. Do not retry or rename this class.')
            try:
                result = self.detector.detect(view, args['region'], args['object_class'], args['reason'], remaining)
            except Exception as exc:
                from .detection import DetectionError
                if isinstance(exc,DetectionError):
                    self.failed_detector_targets[key] = route
                raise
            if result['routing'] != route:
                raise ValueError('Detector routing disagrees with host class policy')
            for proposal in result.get('proposals', []):
                p = dict(proposal, page_id=view['page_id'], source_id=view['source_id'], page=view['page'],
                         query_class=route['canonical_class'], requested_class=args['object_class'])
                if p['source_sha256'] != view['source_sha256'] or p['view_id'] != view['view_id']:
                    raise ValueError('Proposal source/view mismatch')
                if p['id'] in self.proposals and self.proposals[p['id']] != p:
                    raise ValueError('Proposal ID collision')
                self.proposals[p['id']] = p
            result = dict(result, proposals=[self.proposals[p['id']] for p in result.get('proposals', [])])
            self.detections.append({'page_id': view['page_id'], 'routing': route, 'status': result['status'],
                                    'region': args['region'], 'view_id': view['view_id'],
                                    'proposal_ids': [p['id'] for p in result['proposals']]})
            if self.agent_profile and 'detect_objects' in self.schemas:
                from .review import proposal_contexts
                reviews,images=proposal_contexts(self.registry,result['proposals'],self.directory/'proposal-contexts')
                result['review_views']=reviews
                return result,images
            return result, []
        if name == 'predict_room_polygons':
            view = self.registry.checked_view(args['view_id'])
            if view['view_id'] in self.predicted_views:
                raise ValueError('Raster predictions require distinct views')
            self.predicted_views.add(view['view_id'])
            return self.geometry.predict_room_polygons(view, timeout=remaining), []
        if name == 'native_room_candidates':
            self.registry.checked_view(self.registry.initial_for(args['page_id'])['view_id'])
            return self.geometry.native_room_candidates(args['page_id'], args.get('room_identifier'), timeout=remaining), []
        if name == 'read_pdf_vectors':
            from .native_vectors import read_pdf_vectors
            return read_pdf_vectors(self.registry.checked_view(args['view_id']), args.get('cursor',0)), []
        if name == 'read_pdf_text':
            result = read_pdf_text(self.registry.checked_view(args['view_id']), args.get('cursor', 0))
            for line in result['lines']:
                old = self.text_evidence.get(line['id'])
                # Same positioned text may be observed through several views.
                if old and any(old[k] != line[k] for k in ('text', 'box', 'source_sha256', 'page_id')):
                    raise ValueError('Text evidence ID collision')
                self.text_evidence.setdefault(line['id'], line)
            return result, []
        if name == 'calibrate':
            source = self.registry.sources[args['page_id']]
            ident = args['evidence_id']
            if ident in self.registry.views:
                evidence_view = self.registry.checked_view(ident)
                owner, literal, provenance = evidence_view['page_id'], args['reading'], 'model_read_visible_pixels'
            elif ident in self.text_evidence:
                line = self.text_evidence[ident]
                owner, literal, provenance = line['page_id'], line['text'], 'positioned_native_pdf_text'
            else:
                raise ValueError('Unknown visible length evidence ID')
            if owner != args['page_id']:
                raise ValueError('Length evidence belongs to another page')
            if re.search(r'(?:m|ft|cm|mm|in)\s*(?:²|\^\s*2|2\b)|\bsq\.?|\barea\b', literal, re.I):
                raise ValueError('Printed area cannot calibrate geometry')
            if not re.search(r'\d', literal):
                raise ValueError('Visible numeric length required')
            record = {k: args[k] for k in ('kind', 'p0', 'p1', 'length', 'unit')}
            record.update(page_id=owner, source_id=source['source_id'], source_sha256=source['source_sha256'],
                          page=source['page'], description=literal, upstream_evidence_id=ident, reading_provenance=provenance)
            evidence_id = self.geometry.register_length_evidence(record)
            return self.geometry.calibrate(evidence_id, args['p0'], args['p1'], args['length'], args['unit']), []
        if name in ('measure_room', 'measure_distance'):
            result = getattr(self.geometry, name)(**args)
            self.measurements.append(copy.deepcopy({'tool': name, 'arguments': args, 'result': result}))
            return result, []
        raise ValueError('Unimplemented tool')

    def _candidate_page(self, candidate):
        provenance = candidate['provenance']
        matches = [s for s in self.registry.sources.values()
                   if (s['source_id'], s['source_sha256'], s['page']) ==
                   (provenance['source_id'], provenance['source_sha256'], provenance.get('page', 0))]
        if len(matches) != 1:
            raise ValueError('Candidate source/page is not registered')
        return matches[0]

    def bind(self, value, *, strict=True):
        """Resolve registered IDs; salvage valid evidence if final validation fails."""
        if self.agent_profile:
            jsonschema.validate(value, self.final_schema)
            value = self.agent_profile.normalize(value)
        jsonschema.validate(value, FINAL)
        errors, accepted, rooms, seen_rooms, seen_candidates = [], [], [], set(), set()
        for ident in value['accepted_proposal_ids']:
            try:
                p = self.proposals[ident]
                if any(p.get(k) for k in ('clipped', 'touches_crop_edge', 'requires_wider_inspection')):
                    raise ValueError('Edge/clipped proposal needs wider inspection: ' + ident)
                for other in accepted:
                    q = self.proposals[other]
                    if (p['page_id'], p['query_class']) != (q['page_id'], q['query_class']):
                        continue
                    a, b = box(*p['native_box_xyxy']), box(*q['native_box_xyxy'])
                    if a.intersection(b).area / a.union(b).area >= .8:
                        raise ValueError('Near-identical accepted proposals require duplicate resolution: ' + ident)
                accepted.append(ident)
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(str(exc))

        requests, seen_requests = [], set()
        for request in value['count_requests']:
            try:
                pid = request['page_id']
                if pid not in self.registry.sources:
                    raise ValueError('Unknown count page')
                if self.registry.sources[pid].get('native_geometry'):
                    raise ValueError('A room-footprint preview cannot establish source object counts')
                route = self.router.route(request['object_class'])
                key = (pid, route['canonical_class'])
                if key in seen_requests:
                    raise ValueError('Duplicate count request')
                seen_requests.add(key)
                detections = [d for d in self.detections if (d['page_id'], d['routing']['canonical_class']) == key]
                supported = any(d['status'] == 'complete' for d in detections)
                ids = [i for i in accepted if (self.proposals[i]['page_id'], self.proposals[i]['query_class']) == key]
                requests.append({'page_id': pid, 'object_class': route['canonical_class'], 'backend': route['backend'],
                                 'count': len(ids) if supported and value['status'] == 'answered' else None,
                                 'accepted_proposal_ids': ids, 'detector_complete': supported,
                                 'reason': None if supported else 'No complete detector response for this page/class'})
                if not supported:
                    errors.append('Count requires a complete specialist response: ' + route['canonical_class'])
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(str(exc))
        for ident in accepted:
            p = self.proposals[ident]
            if (p['page_id'], p['query_class']) not in seen_requests:
                errors.append('Accepted proposal has no count request: ' + ident)
        # Even an empty detector response must not turn into an invented final answer.
        if self.detections and not requests:
            errors.append('Detector used without a final count request')

        for row in value['rooms']:
            try:
                room = copy.deepcopy(row)
                key = (room['page_id'], room['room_id'])
                if key in seen_rooms or room['candidate_id'] in seen_candidates:
                    raise ValueError('Physical room or candidate selected twice')
                candidate = self.geometry.candidates[room['candidate_id']]
                source = self._candidate_page(candidate)
                if source['page_id'] != room['page_id']:
                    raise ValueError('Room/candidate page mismatch')
                polygon = candidate['polygon']
                shape = Polygon(polygon['exterior'], polygon.get('holes', []))
                if not shape.is_valid or shape.is_empty or shape.area <= 0 or not box(0, 0, source['width'], source['height']).covers(shape):
                    raise ValueError('Invalid or out-of-page room boundary')
                for text_id in room['text_ids']:
                    if self.text_evidence[text_id]['page_id'] != room['page_id']:
                        raise ValueError('Room text belongs to another page')
                room.update(polygon=polygon['exterior'], holes=polygon.get('holes', []),
                            geometry_provenance=candidate['provenance'], quality_flags=candidate.get('quality_flags', []))
                cal = room.get('calibration_id') or (candidate.get('calibration') or {}).get('calibration_id')
                if cal:
                    room['calibration_id'] = cal
                    room['measurement'] = self.geometry.measure_room(room['candidate_id'], cal)
                else:
                    room['measurement'] = {'status': 'unavailable', 'area_m2': None, 'reason': 'No source-bound calibration'}
                room['area_m2'] = room['measurement'].get('area_m2')
                if room['quality_flags']:
                    errors.append('Room geometry has quality flags: ' + room['room_id'])
                seen_rooms.add(key)
                seen_candidates.add(room['candidate_id'])
                rooms.append(room)
            except (KeyError, ValueError, TypeError) as exc:
                errors.append(str(exc))

        associations = []
        for pid in self.registry.sources:
            page_rooms = [r for r in rooms if r['page_id'] == pid]
            page_text = [t for t in self.text_evidence.values() if t['page_id'] == pid]
            items = associate_text(page_text, [{'id': r['room_id'], 'polygon': r['polygon'], 'holes': r['holes']} for r in page_rooms])
            associations.extend(dict(a, page_id=pid) for a in items)
            by_text = {a['text_id']: a for a in items}
            for room in page_rooms:
                room['text_associations'] = [by_text[t] for t in room['text_ids']]
                if room['basis'] == 'text-supported' and (not room['text_ids'] or any(by_text[t]['room_id'] != room['room_id'] for t in room['text_ids'])):
                    room['requested_basis'], room['basis'] = 'text-supported', 'unknown'
                    errors.append('Room classification lacks unique positioned text support: ' + room['room_id'])
        readings = []
        for reading in value.get('readings', []):
            ids = reading['evidence_ids']
            if any(i not in self.registry.views and i not in self.text_evidence for i in ids):
                errors.append('Reading cites unregistered evidence')
            else:
                readings.append(dict(reading, provenance='model_reading', computed_geometry=False))
        selected_measurements = []
        recorded = {entry['result'].get('measurement_id'):entry for entry in self.measurements
                    if entry['result'].get('status') == 'available' and entry['result'].get('measurement_id')}
        for ident in value.get('measurement_ids', []):
            if ident not in recorded:
                errors.append('Unknown or unavailable measurement ID: ' + ident)
            else:
                selected_measurements.append(copy.deepcopy(recorded[ident]))
        if value['status'] == 'answered' and not (requests or rooms or readings or selected_measurements):
            errors.append('An answer requires recorded evidence')
        if errors and strict:
            raise ValueError('; '.join(errors))
        status = 'needs_review' if errors else value['status']
        if status != 'answered':
            for request in requests:
                request['count'] = None
        return {'status': status, 'counts': requests, 'count': requests[0]['count'] if len(requests) == 1 else None,
                'accepted_proposal_ids': accepted, 'proposals': list(self.proposals.values()),
                'rooms': rooms, 'room_count': len(rooms), 'readings': readings, 'notes': value.get('notes', []),
                'text_associations': associations, 'text_evidence': list(self.text_evidence.values()),
                'validation_errors': errors, 'detections': self.detections, 'measurements': selected_measurements,
                'measurement_ids': [entry['result']['measurement_id'] for entry in selected_measurements],
                'coverage': self.inventory()}

    def inventory(self):
        return {'proposal_ids': list(self.proposals), 'candidate_ids': list(self.geometry.candidates),
                'view_ids': list(self.registry.views),
                'calibration_ids': list(self.geometry.calibrations), 'text_ids': list(self.text_evidence),
                'remaining_tools': {k: max(0, self.allowances[k] - v) for k, v in self.uses.items()},
                'vista_views_used': self.registry.inspection_views,
                'sources': [self.registry.public_source(pid) for pid in self.registry.sources]}

    def resources(self):
        return {'responses_remaining': max(0,self.limits.responses-self.responses_used),
                'tools_per_response': self.limits.tools_per_response,
                'vista_views_remaining': max(0,9-self.registry.inspection_views),
                'remaining_tools': {k:max(0,self.allowances[k]-v) for k,v in self.uses.items()},
                'tool_seconds_remaining': None if self.tool_deadline is None else max(0,round(self.tool_deadline-time.monotonic(),1)),
                'failed_requests_consume_allowances': True,
                'failed_detector_targets': list(self.failed_detector_targets.values())}

    def available_tools(self):
        """Do not advertise exhausted operations as available to the model."""
        remaining=self.resources()
        result=[]
        for definition in self.tools:
            name=definition['function']['name']
            if name in self.uses and self.uses[name]>=self.allowances[name]:continue
            if name in ('inspect','read_pixels'):
                if remaining['vista_views_remaining']==0:continue
                definition=copy.deepcopy(definition)
                views=definition['function']['parameters']['properties']['views']
                views['maxItems']=min(views.get('maxItems',remaining['vista_views_remaining']),remaining['vista_views_remaining'])
            result.append(definition)
        return result

    def _save(self, **extra):
        path = self.directory / 'transcript.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'transcript': self.transcript, **extra}, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
        temporary.replace(path)

    def _partial(self, reason):
        value = {'status': 'needs_review', 'accepted_proposal_ids': [], 'count_requests': [], 'rooms': []}
        if self.agent_profile:
            value = {k:v for k,v in value.items() if k in self.final_schema['properties']}
        result = self.bind(value, strict=False)
        result['unavailable_reason'] = reason
        return self._finish(result)

    def _finish(self, result):
        result['evidence_views']={ident:public(view) for ident,view in self.registry.views.items()}
        result['calibrations']=public(list(self.geometry.calibrations.values()))
        result['elapsed_seconds'] = time.monotonic() - self.started
        result['model_calls']=[{k:r.get(k) for k in ('model','actual_model','provider','endpoint','actual_provider','generation_id')} for r in getattr(self.transport,'receipts',[])]
        result['routing_mode']=self.configuration['routing_mode']
        result['configuration'] = {**self.configuration, 'orchestrator': self.configuration['orchestrator']['model'],
                                   'models': {'orchestrator':self.configuration['orchestrator'],'detector':self.configuration['detector']}, 'reasoning': 'high',
                                   'max_output_tokens': 65536, 'dedicated_ocr': False,
                                   'class_policy': self.router.policy, 'limits': self.limits.__dict__}
        if self.agent_profile:
            result['configuration']['agent_profile'] = self.agent_profile.manifest()
        self._save(result=result)
        return result

    def run(self, question):
        if self.started is not None:
            raise RuntimeError('A Lens harness cannot be replayed')
        self.started = time.monotonic()
        self.deadline = self.started + self.limits.deadline_s
        self.tool_deadline = self.deadline - self.limits.final_reserve_s
        context = {'question': question, 'final_schema': self.final_schema,
                                 'resource_budget': self.resources(),
                                 'sources': [self.registry.public_source(pid) for pid in self.registry.sources],
                                 'initial_views': [public(v) for v in self.registry.initial_views]}
        if self.agent_profile:
            context['agent'] = self.agent_profile.name
            context['page_capabilities'] = self.agent_profile.pages
            if 'detect_objects' in self.schemas:
                context['trained_object_classes'] = list(self.router.policy.get('heads',[]))
        content = [json_content(context)]
        if self.conversation_context:
            content.append(json_content({'conversation_context':self.conversation_context,
                'context_policy':'Prior turns only interpret the question. Establish all evidence anew on the currently registered pages; prior IDs are not registered evidence.'}))
        for view in self.registry.initial_views:
            content.extend([json_content({'view_id': view['view_id'], 'page_id': view['page_id']}), image_content(view['path'])])
        messages = [{'role': 'system', 'content': self.system_prompt}, {'role': 'user', 'content': content}]
        force_final = False
        try:
            for index in range(self.limits.responses):
                self._boundary()
                if time.monotonic() >= self.deadline:
                    return self._partial('Trajectory deadline exceeded')
                final = force_final or index == self.limits.responses - 1 or time.monotonic() >= self.tool_deadline
                if final:
                    messages.append({'role': 'user', 'content': 'Finalize now without tools. Use needs_review for insufficient evidence. Recorded inventory: ' + json.dumps(self.inventory())})
                try:
                    self.responses_used = index+1
                    self._emit(operation='response',phase='started',response_index=index,final=final)
                    response = self.transport.complete(messages, [] if final else self.available_tools(),
                                                       self.deadline if final else self.tool_deadline, index, final=final)
                    self._emit(operation='response',phase='completed',response_index=index,final=final)
                    self._boundary()
                except BudgetError as exc:
                    self._emit(operation='response',phase='failed',response_index=index,final=final,reason='Request incomplete. The recorded evidence is available for review.')
                    self.transcript.append({'response_index': index, 'budget_stopped': str(exc)})
                    return self._partial('Request incomplete. The recorded evidence is available for review.')
                except Exception as exc:
                    from .service import Cancelled
                    if isinstance(exc,Cancelled):raise
                    self._emit(operation='response',phase='failed',response_index=index,final=final,error_type=type(exc).__name__)
                    self.transcript.append({'response_index': index, 'error_type': type(exc).__name__, 'error': str(exc)})
                    self._save()
                    if final:
                        return self._partial('Final response unavailable: ' + type(exc).__name__)
                    # Finalization is a new response, not a retry of the failed request.
                    force_final = True
                    messages.append({'role': 'user', 'content': 'Planning response unavailable. Finalize from the recorded evidence only.'})
                    continue
                self.transcript.append({'response_index': index, 'final_phase': final, 'response': response})
                self._save()
                messages.append(response)
                calls = response.get('tool_calls') or []
                if not calls:
                    value = None
                    try:
                        value = parse_object(response.get('content'))
                        return self._finish(self.bind(value))
                    except (ValueError, KeyError, TypeError, jsonschema.ValidationError) as exc:
                        self.transcript.append({'validation_error': str(exc)})
                        self._save()
                        if index < self.limits.responses - 1:
                            force_final = not getattr(exc,'allow_tools',False)
                            messages.append({'role': 'user', 'content': 'Correct this validation error using only recorded IDs: ' + validation_message(exc)})
                            continue
                        try:
                            return self._finish(self.bind(value, strict=False))
                        except (ValueError, KeyError, TypeError, jsonschema.ValidationError):
                            return self._partial('Invalid final JSON or schema')
                pending_images = []
                for call_index, call in enumerate(calls):
                    self._boundary()
                    name = call.get('function', {}).get('name', '')
                    detail={}
                    if name=='detect_objects':
                        # Report the host's class route before dispatch. Invalid
                        # arguments still follow the normal allowance/error path.
                        try:
                            requested=json.loads(call['function']['arguments'])['object_class']
                            route=self.router.route(requested)
                            adapter=getattr(self.detector,route['backend'],None)
                            detail={'routing':route,'model':getattr(adapter,'model',None)}
                        except (KeyError,TypeError,ValueError):pass
                    self._emit(operation='tool',phase='started',tool=name,call_id=call.get('id'),response_index=index,**detail)
                    spending_stopped=False
                    try:
                        if final or call_index >= self.limits.tools_per_response:
                            self._consume(name)
                            if name in ('inspect', 'read_pixels'):
                                self.registry.inspection_views += 1
                            raise ValueError('Tool phase ended' if final else 'Per-response tool allowance exceeded')
                        try:
                            args = json.loads(call['function']['arguments'])
                        except (ValueError, KeyError, TypeError):
                            self._consume(name)
                            if name in ('inspect', 'read_pixels'):
                                self.registry.inspection_views += 1
                            raise ValueError('Invalid tool arguments JSON')
                        result, images = self.tool(name, args)
                    except Exception as exc:
                        result, images = {'error_type': type(exc).__name__, 'error': validation_message(exc)}, []
                        if isinstance(exc,jsonschema.ValidationError):result['raw']={'validation_error':str(exc)}
                        if getattr(exc, 'receipt', None):
                            result['receipt'] = exc.receipt
                        cause=exc
                        while cause is not None:
                            if isinstance(cause,BudgetError):spending_stopped=True;break
                            cause=cause.__cause__
                        if isinstance(exc, (TimeoutError, BudgetError)):
                            force_final = True
                    result = dict(result, remaining_resources=self.resources())
                    self.transcript.append({'tool_call_id': call.get('id'), 'tool': name, 'result': result})
                    self._save()
                    visible={'error_type':'IncompleteRequest','error':'Request incomplete. The recorded evidence is available for review.'} if spending_stopped else public(result)
                    self._emit(operation='tool',phase='completed',tool=name,call_id=call.get('id'),response_index=index,result=visible,**detail)
                    if spending_stopped:return self._partial('Request incomplete. The recorded evidence is available for review.')
                    self._boundary()
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(public(result), ensure_ascii=False)})
                    pending_images.extend(images)
                messages.append({'role': 'user', 'content': [json_content({'remaining_resources':self.resources(),
                    'instruction':'Use only available tools. Stop when the evidence supports an answer or abstention. Final answers must match the supplied JSON schema.'})]+pending_images})
            return self._partial('No valid final response within response allowance')
        except BaseException as exc:
            self._save(interrupted={'type': type(exc).__name__, 'message': str(exc)}, evidence=self.inventory())
            raise
