"""Conservative object-class routing to the fourteen verified D-FINE heads."""
import re
from .dfine import HEADS, verified_head_policy

# Explicit equivalences only; broad or ambiguous terms stay with Argon.
ALIASES={
 'door':'door','doors':'door','single door':'door_single','single doors':'door_single',
 'double door':'door_double','double doors':'door_double','sliding door':'door_sliding','sliding doors':'door_sliding',
 'window':'window','windows':'window','toilet bowl':'toilet_bowl','toilet bowls':'toilet_bowl',
 'toilet':'toilet_bowl','toilets':'toilet_bowl','sink':'sink','sinks':'sink',
 'wc':'toilet_bowl','wc seat':'toilet_bowl','wc seats':'toilet_bowl',
 'toilet bowl (wc seat)':'toilet_bowl','toilet bowls (wc seats)':'toilet_bowl',
 'toilet bowl / wc seat':'toilet_bowl','toilet bowls / wc seats':'toilet_bowl',
 'bathtub':'bathtub','bathtubs':'bathtub','bath tub':'bathtub','bath tubs':'bathtub',
 'shower':'shower','showers':'shower','cooker stove':'cooker_stove','cooker stoves':'cooker_stove',
 'refrigerator':'refrigerator','refrigerators':'refrigerator',
 'washing machine':'washing_machine','washing machines':'washing_machine',
 'dishwasher':'dishwasher','dishwashers':'dishwasher','appliance':'appliance','appliances':'appliance',
}

def normalize(value):
    if not isinstance(value,str) or not value.strip():raise ValueError('Nonempty object class required')
    return re.sub(r'\s+',' ',value.strip().casefold().replace('_',' ').replace('-',' '))

for _head in HEADS:ALIASES[normalize(_head)]=_head

class ClassRouter:
    def __init__(self): self.policy=verified_head_policy()
    def route(self,object_class):
        normalized=normalize(object_class)
        canonical=ALIASES.get(normalized)
        return {'backend':'dfine' if canonical else 'argon','requested_class':object_class,
                'canonical_class':canonical if canonical else normalized,'class_id':HEADS.index(canonical) if canonical else None}

    def accepts_label(self,requested,label):
        """Unknown-class labels must match the requested name or a conservative plural."""
        try: query=self.route(requested); actual=normalize(label)
        except ValueError:return False
        if query['backend']=='dfine':return ALIASES.get(actual)==query['canonical_class']
        wanted=normalize(requested)
        # Explicit morphology only, avoiding English stemming or subtype inference.
        def singular(s):
            if s.endswith('ies') and len(s)>4:return s[:-3]+'y'
            if s.endswith(('ches','shes','xes','zes')):return s[:-2]
            if s.endswith('s') and not s.endswith(('ss','us','is')):return s[:-1]
            return s
        return actual==wanted or singular(actual)==singular(wanted)
