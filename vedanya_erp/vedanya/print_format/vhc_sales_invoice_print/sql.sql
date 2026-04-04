select 
  sii.name as name, 
  sii.item_code as item, 
  coalesce(if(sii.batch_no='', NULL, sii.batch_no), sbe.batch_no) as batch_no, 
  bat.custom_mfg_batch as mfg_batch, 
  DATE_FORMAT(bat.expiry_date, '%m/%y') as expiry_date, 
  coalesce(sbe.qty * -1, if(sii.qty='', NULL, sii.qty)) as qty_raw,
  CASE 
    WHEN MOD(coalesce(sbe.qty * -1, if(sii.qty='', NULL, sii.qty)), 1) = 0 
      THEN FORMAT(coalesce(sbe.qty * -1, if(sii.qty='', NULL, sii.qty)), 0)
    ELSE 
      TRIM(TRAILING '.' FROM TRIM(TRAILING '0' FROM 
        coalesce(sbe.qty * -1, if(sii.qty='', NULL, sii.qty))
      ))
  END as qty
from `tabSales Invoice Item` sii
left join `tabSerial and Batch Entry` sbe on sii.serial_and_batch_bundle = sbe.parent
left join `tabBatch` bat on bat.name = coalesce(if(sii.batch_no='', NULL, sii.batch_no), sbe.batch_no)
where sii.parent = 'SINV-26-00010';