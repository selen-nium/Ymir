"""Conservative deterministic binding for simple one-page object counts."""
import re

# Explicit singular/plural forms only. No subtype inference or room/schedule counts.
CLASSES={
 'door':'door','doors':'door','window':'window','windows':'window',
 'toilet':'toilet_bowl','toilets':'toilet_bowl','toilet bowl':'toilet_bowl','toilet bowls':'toilet_bowl',
 'sink':'sink','sinks':'sink','bathtub':'bathtub','bathtubs':'bathtub',
 'shower':'shower','showers':'shower','appliance':'appliance','appliances':'appliance',
 'single door':'door_single','single doors':'door_single','double door':'door_double','double doors':'door_double',
 'sliding door':'door_sliding','sliding doors':'door_sliding',
 'refrigerator':'refrigerator','refrigerators':'refrigerator',
 'washing machine':'washing_machine','washing machines':'washing_machine',
 'dishwasher':'dishwasher','dishwashers':'dishwasher',
 'cooker stove':'cooker_stove','cooker stoves':'cooker_stove',
}
GROUPS={'bathtubs and showers':['bathtub','shower'],'showers and bathtubs':['shower','bathtub']}
COUNT=re.compile(r'^(?:how many (?P<how>[a-z ]+?) (?:are (?:shown|drawn|there)|are there|exist)|count (?:the )?(?P<count>[a-z ]+?))(?: (?:on|in) (?:this|the) (?:floor plan|plan|drawing|sheet|page))?(?: in total)?[?.!]?$')

def bind_count_scope(question,pages):
    """Return explicit scope only for simple whole-page exact class requests."""
    if not isinstance(question,str) or not isinstance(pages,(list,tuple)) or len(pages)!=1:return None
    page=pages[0]
    if not isinstance(page,dict) or page.get('native_source') or page.get('native_geometry'):return None
    # A single selected sheet from a larger document remains a scoped result; no
    # cross-page conclusions are implied by this binding.
    text=re.sub(r'\s+',' ',question.casefold().strip())
    match=COUNT.fullmatch(text)
    if not match:return None
    name=(match.group('how') or match.group('count')).strip()
    classes=GROUPS.get(name)
    if classes is None:
        canonical=CLASSES.get(name)
        if canonical is None:return None
        classes=[canonical]
    return {'page_index':0,'target_classes':list(classes),'description':name,'backend':'routed'}
