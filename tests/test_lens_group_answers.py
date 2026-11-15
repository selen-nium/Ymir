from ymir.lens_answers import answer_for


def test_grouped_count_remains_one_total_with_readable_label():
    for count, expected in [(1,'I found 1 bathtub or shower.'),
                            (3,'I found 3 bathtubs and showers in total.'),
                            (None,'I couldn’t confirm the bathtub or shower count. Please check the boxes.')]:
        result={'status':'answered' if count is not None else 'needs_review',
                'counts':[{'page_id':'page','object_class':'bathtubs and showers',
                           'object_classes':['bathtub','shower'],'count':count}],
                'proposals':[], 'accepted_proposal_ids':[]}
        answer=answer_for('job',result,{'page':'sheet'})
        assert answer['text']==expected
        assert answer['value']==count
        assert len(answer['counts'])==1
