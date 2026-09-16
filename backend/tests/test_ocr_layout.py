import pytest
from backend.app.ocr_layout import calibrate_confidence, derive_layout_receipt

SPANS=[
 {"span_index":0,"raw_text":"Puerto","confidence":95.0,"page_index":0,"block_num":1,"paragraph_num":1,"line_num":1,"word_num":1,"bbox":{"x":10,"y":10,"width":50,"height":10}},
 {"span_index":1,"raw_text":"Rico","confidence":80.0,"page_index":0,"block_num":1,"paragraph_num":1,"line_num":1,"word_num":2,"bbox":{"x":65,"y":10,"width":30,"height":10}},
 {"span_index":2,"raw_text":"ilegible","confidence":20.0,"page_index":0,"block_num":1,"paragraph_num":1,"line_num":2,"word_num":1,"bbox":{"x":10,"y":30,"width":60,"height":10}},
 {"span_index":3,"raw_text":"?","confidence":None,"page_index":0,"block_num":1,"paragraph_num":1,"line_num":2,"word_num":2,"bbox":{"x":75,"y":30,"width":10,"height":10}},
]

def test_layout_preserves_engine_order_and_closes_spans():
    receipt=derive_layout_receipt(SPANS); assert [line["raw_text"] for line in receipt["lines"]]==["Puerto Rico","ilegible ?"]; assert receipt["reading_order_span_indices"]==[0,1,2,3]; assert receipt["invariants"]["span_accounting_closed"] is True; assert receipt["invariants"]["semantic_tables_inferred"] is False

def test_confidence_preserves_unknown_and_threshold_states():
    receipt=calibrate_confidence(SPANS); assert receipt["counts"]=={"LOW":1,"MEDIUM":1,"HIGH":1,"UNKNOWN":1}; assert receipt["invariants"]["span_accounting_closed"] is True

def test_invalid_thresholds_fail_closed():
    with pytest.raises(ValueError): calibrate_confidence(SPANS,low_threshold=90,high_threshold=80)
