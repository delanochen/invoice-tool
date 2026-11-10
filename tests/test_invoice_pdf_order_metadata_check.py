"""PDF/邮件单据里「工单」元数据行：多工单并列、单工单沿用客户 PO 号。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ''))

from invoice_tool.invoices.pdf import invoice_order_metadata


def test_multi_order_joins_all_numbers_and_falls_back_to_so_number():
    linked = [
        {"order_number": "SO2611001", "client_order_number": "PO-A"},
        {"order_number": "SO2611002", "client_order_number": "PO-B"},
        {"order_number": "SO2611003", "client_order_number": None},
    ]
    assert invoice_order_metadata(linked, None) == ("Service Orders", "PO-A, PO-B, SO2611003")


def test_multi_order_without_any_po_uses_so_numbers():
    linked = [
        {"order_number": "SO1", "client_order_number": None},
        {"order_number": "SO2", "client_order_number": None},
    ]
    assert invoice_order_metadata(linked, None) == ("Service Orders", "SO1, SO2")


def test_single_order_keeps_legacy_service_order_line():
    assert invoice_order_metadata([], {"client_order_number": "PO-A"}) == ("Service Order", "PO-A")
    assert invoice_order_metadata(
        [{"order_number": "SO2611001", "client_order_number": "PO-A"}],
        {"client_order_number": "PO-A"},
    ) == ("Service Order", "PO-A")


def test_no_order_and_no_po_yields_no_line():
    assert invoice_order_metadata([], {"client_order_number": None}) is None
    assert invoice_order_metadata([], None) is None
