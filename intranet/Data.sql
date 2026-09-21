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
    FORMAT(SD.Qty_Usage_Last_12_Months / '12',2) AS `12_Months_Avg_use`,
    IT.QOH,
    IT.QOO,
    Qty_Backordered,

    IFNULL(IT.QOH,0) + IFNULL(IT.QOO,0) - IFNULL(IT.Qty_Backordered,0) AS Net_After_POs,

    CASE
      WHEN IFNULL(IT.QOH,0)+IFNULL(IT.QOO,0)-IFNULL(IT.Qty_Backordered,0) < 0 THEN 'URGENT'
      WHEN IFNULL(IT.QOH,0)+IFNULL(IT.QOO,0)-IFNULL(IT.Qty_Backordered,0) < 5 THEN 'REPLENISH'
      ELSE 'OK'
    END AS Priority,

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

FROM nsexports.SalesMerge

GROUP BY
    Item) SD ON SD.Item = IT.`Name`

    WHERE SD.ItemClass LIKE 'PO%'

    GROUP BY SD.Item