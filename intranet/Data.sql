-- ===========================================================================
-- Item reorder / priority query  (MySQL 5.6 - nsexports)
--
-- Base query by Michael. Marty's changes are marked [CHANGED] / [ADDED] below.
-- Everything not marked is unchanged from the original.
-- ===========================================================================

SELECT 
    SD.YearMth,
    SD.Item,
    SD.Description,
    SD.ItemClass,
    SD.ItemGroup,
    sum(SD.Total_Quantity) AS Total_Quantity,
    SD.Qty_Usage_Last_6_Months,
    FORMAT(SD.Qty_Usage_Last_6_Months / '6',2) AS `6_Months_Avg_use`,
    SD.Qty_Usage_Last_12_Months,

    -- [CHANGED] was dividing Qty_Usage_Last_6_Months by 12, which gave half
    -- the real 12-month average. Now uses the 12-month column.
    FORMAT(SD.Qty_Usage_Last_12_Months / '12',2) AS `12_Months_Avg_use`,

    IT.QOH,
    IT.QOO,
    Qty_Backordered,

    -- [ADDED] Net stock position once outstanding POs land.
    --   on hand + on order - backordered
    -- NOTE: the Streamlit app uses GREATEST(Committed, Back Ordered) here.
    -- nsexports.Items doesn't expose a Committed column, so this only
    -- subtracts Qty_Backordered. If Committed exists under another name,
    -- swap in:  GREATEST(IFNULL(IT.Committed,0), IFNULL(IT.Qty_Backordered,0))
    IFNULL(IT.QOH,0) + IFNULL(IT.QOO,0) - IFNULL(IT.Qty_Backordered,0) AS Net_After_POs,

    -- [ADDED] Urgency banding off the net position.
    --   < 0            -> URGENT    (committed demand exceeds stock + inbound)
    --   < Reorder Point -> REPLENISH (covered, but below where it should sit)
    --   else           -> OK
    -- The 5 is a placeholder reorder point for items that have none set.
    -- Replace with the real column if Items carries one.
    CASE
      WHEN IFNULL(IT.QOH,0)+IFNULL(IT.QOO,0)-IFNULL(IT.Qty_Backordered,0) < 0 THEN 'URGENT'
      WHEN IFNULL(IT.QOH,0)+IFNULL(IT.QOO,0)-IFNULL(IT.Qty_Backordered,0) < 5 THEN 'REPLENISH'
      ELSE 'OK'
    END AS Priority,

    -- [CHANGED] Replaces the original Item_Status CASE, which lived in the
    -- subquery below. It had to move up here: the subquery only sees
    -- SalesMerge, and this needs QOH / QOO / Qty_Backordered from Items.
    --
    -- Reads as: (3 months of demand) - (what we'll have once POs land).
    -- Positive means it needs ordering.
    -- The 3 is months of cover - a user setting in the Streamlit app,
    -- hardcoded here. Change to whatever purchasing actually buy to.
    CASE WHEN CEIL(
        ((IFNULL(SD.Qty_Usage_Last_6_Months,0) / 6) * 3)
        - (IFNULL(IT.QOH,0) + IFNULL(IT.QOO,0) - IFNULL(IT.Qty_Backordered,0))
    ) > 0 THEN 'NEEDED' ELSE 'OK' END AS Item_Status

FROM
    nsexports.Items IT

    LEFT JOIN (SELECT

    YearMth,
    Item,
    Description,
    ItemClass,
    ItemGroup,
    SUM(Quantity) AS Total_Quantity,
    SUM(CASE WHEN TransDate >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH) THEN Quantity ELSE 0  END) AS Qty_Usage_Last_6_Months,
    SUM(CASE WHEN TransDate >= DATE_SUB(CURDATE(), INTERVAL 12 MONTH) THEN Quantity ELSE 0 END) AS Qty_Usage_Last_12_Months

    -- [REMOVED] the original Item_Status CASE sat here. Moved to the outer
    -- SELECT above - it needs stock columns this subquery can't see.
    -- (It also had no comparison operator, so MySQL read any non-zero
    -- result as true and nearly everything came back 'NEEDED'.)

FROM nsexports.SalesMerge

GROUP BY
    Item) SD ON SD.Item = IT.`Name`

    -- NOTE (unchanged, but worth knowing): this WHERE filters on a subquery
    -- column, which turns the LEFT JOIN into an inner join - items with no
    -- sales rows drop out entirely, including dead stock. If Items has its
    -- own ItemClass, filtering on that instead keeps them.
    WHERE SD.ItemClass LIKE 'PO%'

    GROUP BY SD.Item
