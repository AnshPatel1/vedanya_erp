// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Batch-Wise Stock Balance"] = {
  filters: [
    {
      fieldname: "group_by",
      label: __("Group By"),
      fieldtype: "MultiSelectList",
      get_data: function (txt) {
        let options = ["Mfg Batch", "Warehouse", "Item Group", "Item"];
        return options
          .filter(o => o.toLowerCase().includes((txt || "").toLowerCase()))
          .map(o => ({ value: o, description: o }));
      },
    },
    {
      fieldname: "to_date",
      label: __("As On Date"),
      fieldtype: "Date",
      default: frappe.datetime.get_today(),
      reqd: 1,
    },
    {
      fieldname: "warehouse",
      label: __("Warehouses"),
      fieldtype: "MultiSelectList",
      options: "Warehouse",
      get_data: function (txt) {
        const company = frappe.query_report.get_filter_value("company");

        return frappe.db.get_link_options("Warehouse", txt, {
          company: company,
        });
      },
    },
    {
      fieldname: "item_code",
      label: __("Items"),
      fieldtype: "MultiSelectList",
      options: "Item",
      get_data: async function (txt) {
        let { message: data } = await frappe.call({
          method: "erpnext.controllers.queries.item_query",
          args: {
            doctype: "Item",
            txt: txt,
            searchfield: "name",
            start: 0,
            page_len: 10,
            filters: {},
            as_dict: 1,
          },
        });
        data = data.map(({ name, ...rest }) => {
          return {
            value: name,
            description: Object.values(rest),
          };
        });

        return data || [];
      },
    },
    {
      fieldname: "item_group",
      label: __("Item Group"),
      fieldtype: "Link",
      options: "Item Group",
    },
    {
      fieldname: "batch_no",
      label: __("Batch No"),
      fieldtype: "Link",
      options: "Batch"
    },
    {
      fieldname: "custom_mfg_batch",
      label: __("Mfg Batch No"),
      fieldtype: "MultiSelectList",
      get_data: function (txt) {
        return frappe.db.get_link_options("Mfg Batch", txt);
      },
    },
    {
      fieldname: "brand",
      label: __("Brand"),
      fieldtype: "Link",
      options: "Brand",
      hidden: 1
    },
    {
      fieldname: "voucher_no",
      label: __("Voucher #"),
      fieldtype: "Data",
      hidden: 1
    },
    {
      fieldname: "project",
      label: __("Project"),
      fieldtype: "Link",
      options: "Project",
      hidden: 1
    },
    {
      fieldname: "include_uom",
      label: __("Include UOM"),
      fieldtype: "Link",
      options: "UOM",
      hidden: 1
    },
    {
      fieldname: "valuation_field_type",
      label: __("Valuation Field Type"),
      fieldtype: "Select",
      width: "80",
      options: "Currency\nFloat",
      default: "Currency",
    },
    {
      fieldname: "company",
      label: __("Company"),
      fieldtype: "Link",
      options: "Company",
      default: frappe.defaults.get_user_default("Company"),
      reqd: 1,
    },
    {
      fieldname: "enable_valuation",
      label: __("Enable Valuation"),
      fieldtype: "Check"
    }
  ],
  formatter: function (value, row, column, data, default_formatter) {
    value = default_formatter(value, row, column, data);
    if (column.fieldname == "out_qty" && data && data.out_qty < 0) {
      value = "<span style='color:red'>" + value + "</span>";
    } else if (column.fieldname == "in_qty" && data && data.in_qty > 0) {
      value = "<span style='color:green'>" + value + "</span>";
    }

    if (data && data.is_group) {
      value = $(`<span>${value}</span>`).css("font-weight", "bold").wrap("<p></p>").parent().html();
    }

    return value;
  },
  tree: true,
  name_field: "id",
  parent_field: "parent_id",
  initial_depth: 1,
  onload: function (report) {
    let group_by_filter = report.get_filter('group_by');
    if (group_by_filter && (!group_by_filter.get_value() || group_by_filter.get_value().length === 0)) {
      group_by_filter.set_value(['Mfg Batch', 'Item', 'Warehouse']);
    }
    // report.page.add_inner_button(__("View Stock Balance"), function () {
    // 	var filters = report.get_values();
    // 	frappe.set_route("query-report", "Stock Balance", filters);
    // });
  },
};

erpnext.utils.add_inventory_dimensions("Batch-Wise Stock Balance", 10);
