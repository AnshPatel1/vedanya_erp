# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import copy
from collections import defaultdict

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt, get_datetime

from erpnext.stock.doctype.inventory_dimension.inventory_dimension import (
    get_inventory_dimensions,
)
from erpnext.stock.doctype.serial_no.serial_no import get_serial_nos
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import (
    get_stock_balance_for,
)
from erpnext.stock.doctype.warehouse.warehouse import apply_warehouse_filter
from erpnext.stock.utils import (
    is_reposting_item_valuation_in_progress,
    update_included_uom_in_report,
)


def execute(filters=None):
    is_reposting_item_valuation_in_progress()
    include_uom = filters.get("include_uom")
    columns = get_columns(filters)
    items = get_items(filters)
    sl_entries = get_stock_ledger_entries(filters, items)
    item_details = get_item_details(items, sl_entries, include_uom)
    precision = cint(frappe.db.get_single_value("System Settings", "float_precision"))
    bundle_details = get_serial_batch_bundle_details(sl_entries, filters)

    data = []
    conversion_factors = []

    actual_qty = stock_value = 0

    available_serial_nos = {}
    inventory_dimension_filters_applied = check_inventory_dimension_filters_applied(
        filters
    )

    batch_balance_dict = frappe._dict({})
    if actual_qty and filters.get("batch_no"):
        batch_balance_dict[filters.batch_no] = [actual_qty, stock_value]

    for sle in sl_entries:
        item_detail = item_details[sle.item_code]

        sle.update(item_detail)
        if bundle_info := bundle_details.get(sle.serial_and_batch_bundle):
            data.extend(
                get_segregated_bundle_entries(
                    sle, bundle_info, batch_balance_dict, filters
                )
            )
            continue

        if filters.get("batch_no") or inventory_dimension_filters_applied:
            actual_qty += flt(sle.actual_qty, precision)
            stock_value += sle.stock_value_difference
            if sle.batch_no:
                if not batch_balance_dict.get(sle.batch_no):
                    batch_balance_dict[sle.batch_no] = [0, 0]

                batch_balance_dict[sle.batch_no][0] += sle.actual_qty
                batch_balance_dict[sle.batch_no][1] += stock_value

            actual_qty = batch_balance_dict[sle.batch_no][0]
            if sle.voucher_type == "Stock Reconciliation" and not sle.actual_qty:
                actual_qty = sle.qty_after_transaction
                stock_value = sle.stock_value

            sle.update(
                {"qty_after_transaction": actual_qty, "stock_value": stock_value}
            )

        sle.update(
            {"in_qty": max(sle.actual_qty, 0), "out_qty": min(sle.actual_qty, 0)}
        )

        if sle.serial_no:
            update_available_serial_nos(available_serial_nos, sle)

        if sle.actual_qty:
            sle["in_out_rate"] = flt(
                sle.stock_value_difference / sle.actual_qty, precision
            )

        elif sle.voucher_type == "Stock Reconciliation":
            sle["in_out_rate"] = sle.valuation_rate

        data.append(sle)

        if include_uom:
            conversion_factors.append(item_detail.conversion_factor)

    data = group_by_warehouse_and_batch(data)
    enrich_with_batch_details(data)

    if mfg_batches := filters.get("custom_mfg_batch"):
        data = [row for row in data if row.get("custom_mfg_batch") in mfg_batches]

    if filters.get("group_by"):
        columns.insert(
            1,
            {
                "label": _("Group"),
                "fieldname": "group_name",
                "fieldtype": "Data",
                "width": 350,
            },
        )
        data = generate_tree_data(data, filters.get("group_by"))

        group_by_mapping = {
            "Mfg Batch": "custom_mfg_batch",
            "Warehouse": "warehouse",
            "Item Group": "item_group",
            "Item": "item_code",
        }
        hidden_fields = [
            group_by_mapping[g]
            for g in filters.get("group_by")
            if g in group_by_mapping
        ]
        for col in columns:
            if col.get("fieldname") in hidden_fields:
                col["hidden"] = 1
    else:
        for i, row in enumerate(data):
            if not row.get("id"):
                row["id"] = f"row_{i}"
                row["parent_id"] = ""
                row["indent"] = 0

    update_included_uom_in_report(columns, data, include_uom, conversion_factors)
    return columns, data


def generate_tree_data(data, group_by_options):
    group_by_mapping = {
        "Mfg Batch": "custom_mfg_batch",
        "Warehouse": "warehouse",
        "Item Group": "item_group",
        "Item": "item_code",
    }

    group_fields = [
        group_by_mapping[g] for g in group_by_options if g in group_by_mapping
    ]
    if not group_fields:
        for i, row in enumerate(data):
            row["id"] = f"row_{i}"
            row["parent_id"] = ""
            row["indent"] = 0
        return data

    numeric_fields = [
        "in_qty",
        "out_qty",
        "qty_after_transaction",
        "stock_value",
        "stock_value_difference",
    ]

    nodes = {}

    def get_or_create_node(path_tuple):
        if path_tuple in nodes:
            return nodes[path_tuple]

        node_id = "group_" + frappe.generate_hash(length=10)
        parent_tuple = path_tuple[:-1]
        parent_id = ""
        indent = 0
        if parent_tuple:
            parent_node = get_or_create_node(parent_tuple)
            parent_id = parent_node["id"]
            indent = parent_node["indent"] + 1
            if path_tuple not in parent_node["children"]:
                parent_node["children"].append(path_tuple)

        nodes[path_tuple] = {
            "id": node_id,
            "parent_id": parent_id,
            "indent": indent,
            "children": [],
            "leaves": [],
            "path": path_tuple,
            "data": frappe._dict(
                {
                    "id": node_id,
                    "parent_id": parent_id,
                    "indent": indent,
                    "is_group": 1,
                    "has_value": 1,
                    "group_name": path_tuple[-1],
                }
            ),
        }
        return nodes[path_tuple]

    opening_rows = []
    all_keys = set()
    for i, row in enumerate(data):
        all_keys.update(row.keys())
        if row.get("item_code") == _("'Opening'"):
            row["id"] = f"opening_{i}"
            row["parent_id"] = ""
            row["indent"] = 0
            opening_rows.append(row)
            continue

        path = tuple(row.get(f) or "Unassigned" for f in group_fields)
        node = get_or_create_node(path)

        row["id"] = f"leaf_{i}_" + frappe.generate_hash(length=4)
        row["parent_id"] = node["id"]
        row["indent"] = node["indent"] + 1
        row["group_name"] = ""
        node["leaves"].append(row)

    sorted_paths = sorted(nodes.keys(), key=lambda p: len(p), reverse=True)
    all_keys.update(
        [
            "group_name",
            "id",
            "parent_id",
            "indent",
            "is_group",
            "has_value",
            "name",
            "parent",
        ]
    )

    for path in sorted_paths:
        node = nodes[path]
        ndata = node["data"]

        for nf in numeric_fields:
            ndata[nf] = 0.0

        all_children_data = [nodes[c]["data"] for c in node["children"]] + node[
            "leaves"
        ]

        for ch in all_children_data:
            for nf in numeric_fields:
                ndata[nf] += flt(ch.get(nf, 0))

        for k in all_keys:
            if k in numeric_fields or k in [
                "group_name",
                "id",
                "parent_id",
                "indent",
                "is_group",
                "has_value",
                "name",
                "parent",
            ]:
                continue

            if not all_children_data:
                continue

            first_val = all_children_data[0].get(k)
            all_same = True
            for ch in all_children_data[1:]:
                if ch.get(k) != first_val:
                    all_same = False
                    break

            if all_same and first_val is not None:
                ndata[k] = first_val
            else:
                ndata[k] = ""

        for i, val in enumerate(path):
            gf = group_fields[i]
            if val != "Unassigned":
                ndata[gf] = val

    result = opening_rows[:]
    root_paths = [p for p in nodes.keys() if len(p) == 1]

    def traverse(path):
        node = nodes[path]
        result.append(node["data"])
        for child_path in node["children"]:
            traverse(child_path)
        for leaf in node["leaves"]:
            result.append(leaf)

    for p in root_paths:
        traverse(p)

    return result


def group_by_warehouse_and_batch(data):
    """Group rows by (warehouse, batch_no), summing in_qty and out_qty.
    Balance Qty = in_qty + out_qty  (out_qty is stored as a negative number).
    The opening row (if any) is preserved at the top as-is.
    """
    grouped = {}  # key -> aggregated row (frappe._dict)
    key_order = []  # to maintain insertion order

    for row in data:
        # Keep the opening row unchanged
        if row.get("item_code") == _("'Opening'"):
            key_order.append(("__opening__", row.get("batch_no", "")))
            grouped[("__opening__", row.get("batch_no", ""))] = row
            continue

        key = (row.get("warehouse") or "", row.get("batch_no") or "")
        if key not in grouped:
            key_order.append(key)
            # Copy all fields; we will overwrite the aggregated ones
            grouped[key] = frappe._dict(row)
            grouped[key]["in_qty"] = flt(row.get("in_qty", 0))
            grouped[key]["out_qty"] = flt(row.get("out_qty", 0))
        else:
            # Accumulate quantities; keep last-seen values for descriptive fields
            agg = grouped[key]
            agg.update(
                {
                    k: v
                    for k, v in row.items()
                    if k not in ("in_qty", "out_qty", "qty_after_transaction")
                }
            )
            agg["in_qty"] += flt(row.get("in_qty", 0))
            agg["out_qty"] += flt(row.get("out_qty", 0))

    # Compute balance and clean up transaction-level columns
    result = []
    for key in key_order:
        row = grouped[key]
        if key[0] != "__opening__":
            row["qty_after_transaction"] = row["in_qty"] + row["out_qty"]
            # Clear columns that don't make sense in an aggregated row
            row["date"] = None
            row["voucher_type"] = None
            row["voucher_no"] = None
            row["serial_no"] = None
            row["serial_and_batch_bundle"] = None
        result.append(row)

    return result


def enrich_with_batch_details(data):
    """Fetch custom_mfg_batch, manufacturing_date, expiry_date from Batch
    for every unique batch_no present in data and populate the rows."""
    batch_nos = list({row.get("batch_no") for row in data if row.get("batch_no")})
    if not batch_nos:
        return

    batch_details = frappe.get_all(
        "Batch",
        filters={"name": ("in", batch_nos)},
        fields=["name", "custom_mfg_batch", "manufacturing_date", "expiry_date"],
    )
    batch_map = {b.name: b for b in batch_details}

    for row in data:
        batch_no = row.get("batch_no")
        if batch_no and batch_no in batch_map:
            b = batch_map[batch_no]
            row["custom_mfg_batch"] = b.get("custom_mfg_batch")
            row["manufacturing_date"] = b.get("manufacturing_date")
            row["expiry_date"] = b.get("expiry_date")


def get_segregated_bundle_entries(sle, bundle_details, batch_balance_dict, filters):
    segregated_entries = []
    qty_before_transaction = sle.qty_after_transaction - sle.actual_qty
    stock_value_before_transaction = sle.stock_value - sle.stock_value_difference

    for row in bundle_details:
        new_sle = copy.deepcopy(sle)
        new_sle.update(row)
        new_sle.update(
            {
                "in_out_rate": (
                    flt(new_sle.stock_value_difference / row.qty) if row.qty else 0
                ),
                "in_qty": row.qty if row.qty > 0 else 0,
                "out_qty": row.qty if row.qty < 0 else 0,
                "qty_after_transaction": qty_before_transaction + row.qty,
                "stock_value": stock_value_before_transaction
                + new_sle.stock_value_difference,
                "incoming_rate": row.incoming_rate if row.qty > 0 else 0,
            }
        )

        if filters.get("batch_no") and row.batch_no:
            if not batch_balance_dict.get(row.batch_no):
                batch_balance_dict[row.batch_no] = [0, 0]

            batch_balance_dict[row.batch_no][0] += row.qty
            batch_balance_dict[row.batch_no][1] += row.stock_value_difference

            new_sle.update(
                {
                    "qty_after_transaction": batch_balance_dict[row.batch_no][0],
                    "stock_value": batch_balance_dict[row.batch_no][1],
                }
            )

        qty_before_transaction += row.qty
        stock_value_before_transaction += new_sle.stock_value_difference

        new_sle.valuation_rate = (
            stock_value_before_transaction / qty_before_transaction
            if qty_before_transaction
            else 0
        )

        segregated_entries.append(new_sle)

    return segregated_entries


def get_serial_batch_bundle_details(sl_entries, filters=None):
    bundle_details = []
    for sle in sl_entries:
        if sle.serial_and_batch_bundle:
            bundle_details.append(sle.serial_and_batch_bundle)

    if not bundle_details:
        return frappe._dict({})

    query_filers = {"parent": ("in", bundle_details)}
    if filters.get("batch_no"):
        query_filers["batch_no"] = filters.batch_no

    _bundle_details = frappe._dict({})
    batch_entries = frappe.get_all(
        "Serial and Batch Entry",
        filters=query_filers,
        fields=[
            "parent",
            "qty",
            "incoming_rate",
            "stock_value_difference",
            "batch_no",
            "serial_no",
        ],
        order_by="parent, idx",
    )
    for entry in batch_entries:
        _bundle_details.setdefault(entry.parent, []).append(entry)

    return _bundle_details


def update_available_serial_nos(available_serial_nos, sle):
    serial_nos = get_serial_nos(sle.serial_no)
    key = (sle.item_code, sle.warehouse)
    if key not in available_serial_nos:
        stock_balance = get_stock_balance_for(
            sle.item_code, sle.warehouse, sle.posting_date, sle.posting_time
        )
        serials = (
            get_serial_nos(stock_balance["serial_nos"])
            if stock_balance["serial_nos"]
            else []
        )
        available_serial_nos.setdefault(key, serials)

    existing_serial_no = available_serial_nos[key]
    for sn in serial_nos:
        if sle.actual_qty > 0:
            if sn in existing_serial_no:
                existing_serial_no.remove(sn)
            else:
                existing_serial_no.append(sn)
        else:
            if sn in existing_serial_no:
                existing_serial_no.remove(sn)
            else:
                existing_serial_no.append(sn)

    sle.balance_serial_no = "\n".join(existing_serial_no)


def get_columns(filters):
    columns = [
        {
            "label": _("Date"),
            "fieldname": "date",
            "fieldtype": "Datetime",
            "width": 150,
            "hidden": 1,
        },
        {
            "label": _("Item"),
            "fieldname": "item_code",
            "fieldtype": "Link",
            "options": "Item",
            "width": 250,
        },
        {
            "label": _("Item Name"),
            "fieldname": "item_name",
            "width": 100,
            "hidden": 1,
        },
        {
            "label": _("Batch"),
            "fieldname": "batch_no",
            "fieldtype": "Link",
            "options": "Batch",
            "width": 200,
            "hidden": 1,
        },
        {
            "label": _("Mfg Batch"),
            "fieldname": "custom_mfg_batch",
            "fieldtype": "Link",
            "options": "Mfg Batch",
            "width": 130,
        },
    ]

    for dimension in get_inventory_dimensions():
        columns.append(
            {
                "label": _(dimension.doctype),
                "fieldname": dimension.fieldname,
                "fieldtype": "Link",
                "options": dimension.doctype,
                "width": 110,
            }
        )

    columns.extend(
        [
            {
                "label": _("In Qty"),
                "fieldname": "in_qty",
                "fieldtype": "Float",
                "width": 80,
                "convertible": "qty",
                "hidden": 1,
            },
            {
                "label": _("Out Qty"),
                "fieldname": "out_qty",
                "fieldtype": "Float",
                "width": 80,
                "convertible": "qty",
                "hidden": 1,
            },
            {
                "label": _("Balance Qty"),
                "fieldname": "qty_after_transaction",
                "fieldtype": "Float",
                "width": 100,
                "convertible": "qty",
            },
            {
                "label": _("Stock UOM"),
                "fieldname": "stock_uom",
                "fieldtype": "Link",
                "options": "UOM",
                "width": 90,
            },
            {
                "label": _("Mfg Date"),
                "fieldname": "manufacturing_date",
                "fieldtype": "Date",
                "width": 110,
            },
            {
                "label": _("Exp Date"),
                "fieldname": "expiry_date",
                "fieldtype": "Date",
                "width": 110,
            },
            {
                "label": _("Warehouse"),
                "fieldname": "warehouse",
                "fieldtype": "Link",
                "options": "Warehouse",
                "width": 150,
            },
            {
                "label": _("Item Group"),
                "fieldname": "item_group",
                "fieldtype": "Link",
                "options": "Item Group",
                "width": 200,
            },
            {
                "label": _("Description"),
                "fieldname": "description",
                "width": 200,
                "hidden": 1,
            },
            {
                "label": _("Incoming Rate"),
                "fieldname": "incoming_rate",
                "fieldtype": "Currency",
                "width": 110,
                "options": "Company:company:default_currency",
                "convertible": "rate",
                "hidden": not filters.get("enable_valuation", False),
            },
            {
                "label": _("Avg Rate (Balance Stock)"),
                "fieldname": "valuation_rate",
                "fieldtype": filters.valuation_field_type,
                "width": 180,
                "options": (
                    "Company:company:default_currency"
                    if filters.valuation_field_type == "Currency"
                    else None
                ),
                "convertible": "rate",
                "hidden": not filters.get("enable_valuation", False),
            },
            {
                "label": _("Valuation Rate"),
                "fieldname": "in_out_rate",
                "fieldtype": filters.valuation_field_type,
                "width": 140,
                "options": (
                    "Company:company:default_currency"
                    if filters.valuation_field_type == "Currency"
                    else None
                ),
                "convertible": "rate",
                "hidden": not filters.get("enable_valuation", False),
            },
            {
                "label": _("Balance Value"),
                "fieldname": "stock_value",
                "fieldtype": "Currency",
                "width": 110,
                "options": "Company:company:default_currency",
                "hidden": not filters.get("enable_valuation", False),
            },
            {
                "label": _("Value Change"),
                "fieldname": "stock_value_difference",
                "fieldtype": "Currency",
                "width": 110,
                "options": "Company:company:default_currency",
                "hidden": not filters.get("enable_valuation", False),
            },
            {
                "label": _("Voucher Type"),
                "fieldname": "voucher_type",
                "width": 110,
                "hidden": 1,
            },
            {
                "label": _("Voucher #"),
                "fieldname": "voucher_no",
                "fieldtype": "Dynamic Link",
                "options": "voucher_type",
                "width": 100,
                "hidden": 1,
            },
            {
                "label": _("Serial No"),
                "fieldname": "serial_no",
                "fieldtype": "Link",
                "options": "Serial No",
                "width": 100,
                "hidden": 1,
            },
            {
                "label": _("Serial and Batch Bundle"),
                "fieldname": "serial_and_batch_bundle",
                "fieldtype": "Link",
                "options": "Serial and Batch Bundle",
                "width": 100,
                "hidden": 1,
            },
            {
                "label": _("Project"),
                "fieldname": "project",
                "fieldtype": "Link",
                "options": "Project",
                "width": 100,
                "hidden": 1,
            },
            {
                "label": _("Company"),
                "fieldname": "company",
                "fieldtype": "Link",
                "options": "Company",
                "width": 110,
            },
            {
                "label": _("Brand"),
                "fieldname": "brand",
                "fieldtype": "Link",
                "options": "Brand",
                "width": 100,
                "hidden": 1,
            },
        ]
    )

    return columns


def get_stock_ledger_entries(filters, items):
    to_date = get_datetime(filters.to_date + " 23:59:59")

    sle = frappe.qb.DocType("Stock Ledger Entry")
    query = (
        frappe.qb.from_(sle)
        .select(
            sle.item_code,
            sle.posting_datetime.as_("date"),
            sle.warehouse,
            sle.posting_date,
            sle.posting_time,
            sle.actual_qty,
            sle.incoming_rate,
            sle.valuation_rate,
            sle.company,
            sle.voucher_type,
            sle.qty_after_transaction,
            sle.stock_value_difference,
            sle.serial_and_batch_bundle,
            sle.voucher_no,
            sle.stock_value,
            sle.batch_no,
            sle.serial_no,
            sle.project,
        )
        .where(
            (sle.docstatus < 2)
            & (sle.is_cancelled == 0)
            & (sle.posting_datetime <= to_date)
        )
        .orderby(sle.posting_datetime)
        .orderby(sle.creation)
    )

    inventory_dimension_fields = get_inventory_dimension_fields()
    if inventory_dimension_fields:
        for fieldname in inventory_dimension_fields:
            query = query.select(fieldname)
            if fieldname in filters and filters.get(fieldname):
                query = query.where(sle[fieldname].isin(filters.get(fieldname)))

    if items:
        query = query.where(sle.item_code.isin(items))

    for field in ["voucher_no", "project", "company"]:
        if filters.get(field) and field not in inventory_dimension_fields:
            query = query.where(sle[field] == filters.get(field))

    if filters.get("batch_no"):
        bundles = get_serial_and_batch_bundles(filters)

        if bundles:
            query = query.where(
                (sle.serial_and_batch_bundle.isin(bundles))
                | (sle.batch_no == filters.batch_no)
            )
        else:
            query = query.where(sle.batch_no == filters.batch_no)

    query = apply_warehouse_filter(query, sle, filters)

    return query.run(as_dict=True)


def get_serial_and_batch_bundles(filters):
    SBB = frappe.qb.DocType("Serial and Batch Bundle")
    SBE = frappe.qb.DocType("Serial and Batch Entry")

    query = (
        frappe.qb.from_(SBE)
        .inner_join(SBB)
        .on(SBE.parent == SBB.name)
        .select(SBE.parent)
        .where(
            (SBB.docstatus == 1)
            & (SBB.has_batch_no == 1)
            & (SBB.voucher_no.notnull())
            & (SBE.batch_no == filters.batch_no)
        )
    )

    return query.run(pluck=SBE.parent)


def get_inventory_dimension_fields():
    return [dimension.fieldname for dimension in get_inventory_dimensions()]


def get_items(filters):
    item = frappe.qb.DocType("Item")
    query = frappe.qb.from_(item).select(item.name)
    conditions = []

    if item_codes := filters.get("item_code"):
        conditions.append(item.name.isin(item_codes))

    else:
        if brand := filters.get("brand"):
            conditions.append(item.brand == brand)

        if filters.get("item_group") and (
            condition := get_item_group_condition(filters.get("item_group"), item)
        ):
            conditions.append(condition)

    items = []
    if conditions:
        for condition in conditions:
            query = query.where(condition)

        items = [r[0] for r in query.run()]

    return items


def get_item_details(items, sl_entries, include_uom):
    item_details = {}
    if not items:
        items = list(set(d.item_code for d in sl_entries))

    if not items:
        return item_details

    item = frappe.qb.DocType("Item")
    query = (
        frappe.qb.from_(item)
        .select(
            item.name,
            item.item_name,
            item.description,
            item.item_group,
            item.brand,
            item.stock_uom,
        )
        .where(item.name.isin(items))
    )

    if include_uom:
        ucd = frappe.qb.DocType("UOM Conversion Detail")
        query = (
            query.left_join(ucd)
            .on((ucd.parent == item.name) & (ucd.uom == include_uom))
            .select(ucd.conversion_factor)
        )

    res = query.run(as_dict=True)

    for item in res:
        item_details.setdefault(item.name, item)

    return item_details


# TODO: THIS IS NOT USED
def get_sle_conditions(filters):
    conditions = []
    if filters.get("warehouse"):
        warehouse_condition = get_warehouse_condition(filters.get("warehouse"))
        if warehouse_condition:
            conditions.append(warehouse_condition)
    if filters.get("voucher_no"):
        conditions.append("voucher_no=%(voucher_no)s")
    if filters.get("batch_no"):
        conditions.append("batch_no=%(batch_no)s")
    if filters.get("project"):
        conditions.append("project=%(project)s")

    for dimension in get_inventory_dimensions():
        if filters.get(dimension.fieldname):
            conditions.append(f"{dimension.fieldname} in %({dimension.fieldname})s")

    return "and {}".format(" and ".join(conditions)) if conditions else ""


def get_opening_balance_from_batch(filters, columns, sl_entries):
    query_filters = {
        "batch_no": filters.batch_no,
        "docstatus": 1,
        "is_cancelled": 0,
        "posting_date": ("<", filters.from_date),
        "company": filters.company,
    }

    for fields in ["item_code", "warehouse"]:
        if value := filters.get(fields):
            query_filters[fields] = ("in", value)

    opening_data = frappe.get_all(
        "Stock Ledger Entry",
        fields=[
            {"SUM": "actual_qty", "as": "qty_after_transaction"},
            {"SUM": "stock_value_difference", "as": "stock_value"},
        ],
        filters=query_filters,
    )[0]

    for field in ["qty_after_transaction", "stock_value", "valuation_rate"]:
        if opening_data.get(field) is None:
            opening_data[field] = 0.0

    table = frappe.qb.DocType("Stock Ledger Entry")
    sabb_table = frappe.qb.DocType("Serial and Batch Entry")
    query = (
        frappe.qb.from_(table)
        .inner_join(sabb_table)
        .on(table.serial_and_batch_bundle == sabb_table.parent)
        .select(
            Sum(sabb_table.qty).as_("qty"),
            Sum(sabb_table.stock_value_difference).as_("stock_value"),
        )
        .where(
            (sabb_table.batch_no == filters.batch_no)
            & (sabb_table.docstatus == 1)
            & (table.posting_date < filters.from_date)
            & (table.is_cancelled == 0)
        )
    )

    for field in ["item_code", "warehouse", "company"]:
        value = filters.get(field)

        if not value:
            continue

        if isinstance(value, list | tuple):
            query = query.where(table[field].isin(value))

        else:
            query = query.where(table[field] == value)

    bundle_data = query.run(as_dict=True)

    if bundle_data:
        opening_data.qty_after_transaction += flt(bundle_data[0].qty)
        opening_data.stock_value += flt(bundle_data[0].stock_value)
        if opening_data.qty_after_transaction:
            opening_data.valuation_rate = flt(opening_data.stock_value) / flt(
                opening_data.qty_after_transaction
            )

    return {
        "item_code": _("'Opening'"),
        "qty_after_transaction": opening_data.qty_after_transaction,
        "valuation_rate": opening_data.valuation_rate,
        "stock_value": opening_data.stock_value,
    }


def get_opening_balance(filters, columns, sl_entries):
    if not (filters.item_code and filters.warehouse and filters.from_date):
        return

    from erpnext.stock.stock_ledger import get_previous_sle

    last_entry = get_previous_sle(
        {
            "item_code": filters.item_code,
            "warehouse_condition": get_warehouse_condition(filters.warehouse),
            "posting_date": filters.from_date,
            "posting_time": "00:00:00",
        }
    )

    # check if any SLEs are actually Opening Stock Reconciliation
    for sle in list(sl_entries):
        if (
            sle.get("voucher_type") == "Stock Reconciliation"
            and sle.posting_date == filters.from_date
            and frappe.db.get_value("Stock Reconciliation", sle.voucher_no, "purpose")
            == "Opening Stock"
        ):
            last_entry = sle
            sl_entries.remove(sle)

    row = {
        "item_code": _("'Opening'"),
        "qty_after_transaction": last_entry.get("qty_after_transaction", 0),
        "valuation_rate": last_entry.get("valuation_rate", 0),
        "stock_value": last_entry.get("stock_value", 0),
    }

    return row


def get_warehouse_condition(warehouses):
    if not warehouses:
        return ""

    if isinstance(warehouses, str):
        warehouses = [warehouses]

    warehouse_range = frappe.get_all(
        "Warehouse",
        filters={
            "name": ("in", warehouses),
        },
        fields=["lft", "rgt"],
        as_list=True,
    )

    if not warehouse_range:
        return ""

    alias = "wh"
    conditions = []
    for lft, rgt in warehouse_range:
        conditions.append(f"({alias}.lft >= {lft} and {alias}.rgt <= {rgt})")

    conditions = " or ".join(conditions)

    return f" exists (select name from `tabWarehouse` {alias} \
		where ({conditions}) and warehouse = {alias}.name)"


def get_item_group_condition(item_group, item_table=None):
    item_group_details = frappe.db.get_value(
        "Item Group", item_group, ["lft", "rgt"], as_dict=1
    )
    if item_group_details:
        if item_table:
            ig = frappe.qb.DocType("Item Group")
            return item_table.item_group.isin(
                frappe.qb.from_(ig)
                .select(ig.name)
                .where(
                    (ig.lft >= item_group_details.lft)
                    & (ig.rgt <= item_group_details.rgt)
                    & (item_table.item_group == ig.name)
                )
            )
        else:
            return f"item.item_group in (select ig.name from `tabItem Group` ig \
				where ig.lft >= {item_group_details.lft} and ig.rgt <= {item_group_details.rgt} and item.item_group = ig.name)"


def check_inventory_dimension_filters_applied(filters) -> bool:
    for dimension in get_inventory_dimensions():
        if dimension.fieldname in filters and filters.get(dimension.fieldname):
            return True

    return False
