frappe.ui.form.BatchQuickEntryForm = class BatchQuickEntryForm extends (
  frappe.ui.form.QuickEntryForm
) {
  render_dialog() {
    super.render_dialog();
    this._setup_batch_id_watchers();
    this._setup_show_stock_balances();
  }

  _setup_batch_id_watchers() {
    const update = () => this._update_batch_id();

    // Frappe dialog field controls call df.onchange after committing
    // the value into dialog.doc — this is the correct hook point.
    for (const fieldname of ["custom_mfg_batch", "item"]) {
      const field = this.dialog.fields_dict[fieldname];
      if (!field) continue;

      // Wrap any existing onchange so we don't clobber other handlers.
      const prev = field.df.onchange ? field.df.onchange.bind(field) : null;
      field.df.onchange = () => {
        prev && prev();
        update();
      };

      // Belt-and-braces: also listen on the underlying jQuery input so
      // that link fields (which fire awesomplete:select) are caught too.
      if (field.$input) {
        field.$input.on("awesomplete-select awesomplete-selectcomplete", () => {
          // value lands in doc on next tick after the select event
          setTimeout(update, 0);
        });
      }
    }
  }

  _update_batch_id() {
    const doc = this.dialog.doc;
    if (!doc) return;

    const val = doc.custom_mfg_batch || null;
    if (!val || !doc.item) return;

    const parts = doc.item.split(" ");
    const product = parts[0].toUpperCase();
    const suffix = parts.find((s) => !isNaN(s) && !isNaN(parseFloat(s)));

    const full_batch_id = [val, product, suffix].filter(Boolean).join("-");

    if (doc.batch_id === full_batch_id) return;

    doc.batch_id = full_batch_id;

    const batch_id_field = this.dialog.fields_dict["batch_id"];
    if (batch_id_field) {
      batch_id_field.set_input(full_batch_id);
    }
  }

  _setup_show_stock_balances() {
    let btn_field = this.dialog.fields_dict.custom_show_stock_balances;
    if (!btn_field) return;

    // QuickEntry dialog fields don't run the standard frm events, so we bind the click manually
    if (btn_field.$input) {
      btn_field.$input.off('click').on('click', () => {
        this._show_stock_balances();
      });
    } else {
      btn_field.df.click = () => this._show_stock_balances();
    }
  }

  _show_stock_balances() {
    let doc = this.dialog.doc;
    if (!doc.item) {
      frappe.msgprint(__("Please select an Item first."));
      return;
    }

    let filters = {
      item_code: JSON.stringify([doc.item])
    };

    let query_string = new URLSearchParams(filters).toString();
    let url = "/app/query-report/Batch-Wise Stock Balance?" + query_string;

    let d = new frappe.ui.Dialog({
      title: __("Batch-Wise Stock Balances"),
      size: "extra-large",
      fields: [
        {
          fieldtype: "HTML",
          fieldname: "report_html"
        }
      ],
      primary_action_label: __("Close"),
      primary_action: function () {
        d.hide();
      }
    });

    let iframe = document.createElement("iframe");
    iframe.src = url;
    iframe.style.cssText = "width:100%; height:80vh; border:none; display:block;";

    iframe.addEventListener("load", function () {
      try {
        let iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
        let iframeWin = iframe.contentWindow;

        let style = iframeDoc.createElement("style");
        style.textContent = `.body-sidebar-container { display: none !important; }`;
        iframeDoc.head.appendChild(style);

        let observer = new iframeWin.MutationObserver(function () {
          let sidebar = iframeDoc.querySelector(".body-sidebar-container");
          if (sidebar) {
            sidebar.style.setProperty("display", "none", "important");
          }
        });
        observer.observe(iframeDoc.documentElement, {
          childList: true,
          subtree: true,
        });

        iframeWin.setTimeout(function () { observer.disconnect(); }, 15000);

      } catch (e) {
        console.warn("iframe sidebar hide error:", e);
      }
    });

    let header_html = `
      <div class="last-mfg-batch-info" style="margin-bottom: 16px; padding: 10px; font-weight: bold; font-size: 14px; text-align: center; color: var(--text-color); border-bottom: 1px solid var(--border-color); border-radius: 4px 4px 0 0; background-color: var(--control-bg);">
        ${__("Fetching Last Mfg Batch No...")}
      </div>
    `;

    d.fields_dict.report_html.$wrapper.html(header_html).append(iframe);

    frappe.db.get_list('Mfg Batch', {
      fields: ['name'],
      limit: 1,
      order_by: 'creation desc'
    }).then(records => {
      let last_batch = records && records.length ? records[0].name : __("None");
      d.fields_dict.report_html.$wrapper.find('.last-mfg-batch-info').html(
        `${__("Last Mfg Batch No")}: <span style="user-select: all;">${last_batch}</span>`
      );
    });

    d.show();
  }
};
