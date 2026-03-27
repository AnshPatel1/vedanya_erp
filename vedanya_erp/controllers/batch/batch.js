frappe.ui.form.on('Batch', {
  custom_mfg_batch: function (frm) {
    update_batch_id(frm)
  },
  item: function (frm) {
    update_batch_id(frm)
  },
  custom_show_stock_balances: function (frm) {
    let filters = {
      item_code: JSON.stringify([frm.doc.item])
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

    // Frappe is a SPA: the `load` event fires when the JS bundle loads,
    // but the route (and its DOM) renders asynchronously afterward.
    // Strategy: inject a <style> early AND use MutationObserver to
    // force-hide the sidebar element the instant Frappe creates it.
    iframe.addEventListener("load", function () {
      try {
        let iframeDoc = iframe.contentDocument || iframe.contentWindow.document;
        let iframeWin = iframe.contentWindow;

        // Inject a <style> into the iframe's <head> early.
        // CSS rules apply lazily, so this will also catch elements
        // Frappe adds after the route renders.
        let style = iframeDoc.createElement("style");
        style.textContent = `.body-sidebar-container { display: none !important; }`;
        iframeDoc.head.appendChild(style);

        // Belt-and-suspenders: also use MutationObserver scoped to the
        // iframe window so we can force an inline style the moment the
        // element appears (overrides any JS that re-shows it).
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

        // Disconnect after 15 s once the page has fully settled
        iframeWin.setTimeout(function () { observer.disconnect(); }, 15000);

      } catch (e) {
        // same-domain, so this should never fire
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
        `${__("Last Mfg Batch No")}: <u><b>${last_batch}</b></u>`
      );
    });

    d.show();
  }
});


function update_batch_id(frm) {
  console.log("IN")
  if (!frm) return;

  const val = frm.doc.custom_mfg_batch || null;
  if (!val || !frm.doc.item) return;

  const suffix_find = frm.doc.item
    .split(' ')
    .filter(ss => !isNaN(ss) && !isNaN(parseFloat(ss)));

  const suffix = suffix_find && suffix_find[0];
  const product = frm.doc.item.split(' ')[0].toUpperCase();
  const full_batch_id = [val, product, suffix].filter(Boolean).join('-');

  if (val && full_batch_id && frm.doc.batch_id !== full_batch_id) {
    frm.set_value('batch_id', full_batch_id);
  }
}
