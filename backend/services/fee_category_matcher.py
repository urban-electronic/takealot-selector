"""
Fee 品类匹配服务
按优先级: 分类路径精确匹配 → 关键词匹配 → 标题匹配 → 默认
"""

import re
from typing import Optional, List, Dict
from sqlalchemy.orm import Session
from models import FeeMappingRule, FeeCategory


def match_fee_category(
    db: Session,
    takealot_category_path: Optional[str] = None,
    product_name: Optional[str] = None,
) -> Dict:
    """
    返回推荐结果:
    {
        "fee_category": str | None,
        "confidence": "high" | "medium" | "low",
        "match_reason": str
    }
    """
    rules = (
        db.query(FeeMappingRule)
        .filter(FeeMappingRule.active == True)
        .order_by(FeeMappingRule.priority.desc())
        .all()
    )

    # 1. 分类路径精确匹配(最高优先级)
    if takealot_category_path:
        for rule in rules:
            if rule.takealot_category_pattern:
                pattern = rule.takealot_category_pattern.strip()
                if pattern.lower() in takealot_category_path.lower():
                    return {
                        "fee_category": rule.fee_category,
                        "confidence": "high",
                        "match_reason": f"Takealot 分类路径匹配: {pattern}",
                    }

    # 2. 分类路径关键词匹配
    if takealot_category_path:
        keywords = _extract_keywords(takealot_category_path)
        for rule in rules:
            if rule.takealot_category_pattern:
                rule_keywords = _extract_keywords(rule.takealot_category_pattern)
                if _keyword_overlap(keywords, rule_keywords) >= 2:
                    return {
                        "fee_category": rule.fee_category,
                        "confidence": "medium",
                        "match_reason": f"关键词匹配: {rule.takealot_category_pattern}",
                    }

    # 3. 标题关键词匹配
    if product_name:
        for rule in rules:
            if rule.title_keyword_pattern:
                pattern = rule.title_keyword_pattern.strip()
                if pattern.lower() in product_name.lower():
                    return {
                        "fee_category": rule.fee_category,
                        "confidence": "medium",
                        "match_reason": f"标题关键词匹配: {pattern}",
                    }

    # 4. 无匹配
    return {
        "fee_category": None,
        "confidence": "low",
        "match_reason": "无高置信度匹配,建议人工确认",
    }


def _extract_keywords(text: str) -> List[str]:
    """从文本中提取关键词(按空格、&、>等分隔)"""
    return re.split(r'[\s&>/,]+', text.lower())


def _keyword_overlap(kw1: List[str], kw2: List[str]) -> int:
    """计算两个关键词列表的重叠数"""
    set1 = set(k.strip() for k in kw1 if len(k.strip()) > 1)
    set2 = set(k.strip() for k in kw2 if len(k.strip()) > 1)
    return len(set1 & set2)
