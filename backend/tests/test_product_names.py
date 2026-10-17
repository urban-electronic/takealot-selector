from types import SimpleNamespace
import pytest
from services.product_names import chinese_name

@pytest.mark.parametrize('skus,cn,sku,expected', [
    ('a','吸尘器袋','a',('吸尘器袋','ok')),
    ('a','Vacuum Bags','a',(None,'invalid')),
    ('a b','项链金色 项链银色','b',('项链银色','ok')),
    ('a b','项链','b',(None,'ambiguous')),
    ('a a','金色 银色','a',(None,'ambiguous')),
    ('a','','a',(None,'missing')),
    ('a b','金色 Silver','b',(None,'invalid')),
])
def test_chinese_name_matches_exact_sku(skus,cn,sku,expected):
    assert chinese_name(SimpleNamespace(sku=skus,chinese_product_name=cn),sku)==expected
