DROP VIEW IF EXISTS bench.v_provider_outliers CASCADE;
DROP VIEW IF EXISTS bench.v_state_specialty_benchmark CASCADE;
DROP VIEW IF EXISTS bench.v_specialty_yoy CASCADE;
DROP VIEW IF EXISTS bench.v_specialty_year CASCADE;
DROP VIEW IF EXISTS bench.v_national_year CASCADE;
DROP VIEW IF EXISTS bench.v_provider_metrics CASCADE;

-- Provider-level metrics. CMS suppresses cells with <11 beneficiaries, so we
-- keep the same floor to avoid unstable per-beneficiary ratios.
CREATE VIEW bench.v_provider_metrics AS
SELECT data_year, npi, provider_type, state,
       total_benes, total_srvcs, total_payment, total_allowed,
       total_submitted_charges, avg_risk_score, drug_payment, entity_code,
       total_payment / NULLIF(total_benes, 0)            AS payment_per_bene,
       total_srvcs   / NULLIF(total_benes, 0)            AS srvcs_per_bene,
       total_submitted_charges / NULLIF(total_allowed, 0) AS charge_to_allowed
FROM bench.provider_year
WHERE total_benes >= 11;

CREATE VIEW bench.v_national_year AS
SELECT data_year,
       count(*)                      AS providers,
       sum(total_benes)              AS benes,
       sum(total_payment)            AS payment,
       sum(total_payment) / NULLIF(sum(total_benes), 0) AS payment_per_bene
FROM bench.v_provider_metrics
GROUP BY data_year;

CREATE VIEW bench.v_specialty_year AS
SELECT data_year, provider_type,
       count(*)                       AS providers,
       sum(total_benes)               AS benes,
       sum(total_srvcs)               AS srvcs,
       sum(total_payment)             AS payment,
       sum(total_payment) / NULLIF(sum(total_benes), 0)  AS payment_per_bene_weighted,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY payment_per_bene)  AS median_payment_per_bene,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY total_payment)     AS median_payment_per_provider,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY srvcs_per_bene)    AS median_srvcs_per_bene,
       avg(avg_risk_score)            AS avg_risk_score
FROM bench.v_provider_metrics
GROUP BY data_year, provider_type;

CREATE VIEW bench.v_specialty_yoy AS
SELECT cur.data_year, cur.provider_type,
       cur.providers, cur.payment, cur.median_payment_per_bene,
       prev.data_year AS prior_year,
       (cur.providers - prev.providers)::numeric / NULLIF(prev.providers, 0) AS providers_yoy_pct,
       (cur.payment   - prev.payment)   / NULLIF(prev.payment, 0)            AS payment_yoy_pct,
       (cur.median_payment_per_bene - prev.median_payment_per_bene)
           / NULLIF(prev.median_payment_per_bene, 0)                          AS median_ppb_yoy_pct
FROM bench.v_specialty_year cur
JOIN bench.v_specialty_year prev
  ON prev.provider_type = cur.provider_type AND prev.data_year = cur.data_year - 1;

CREATE VIEW bench.v_state_specialty_benchmark AS
WITH nat AS (
    SELECT data_year, provider_type,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY payment_per_bene) AS national_median_ppb
    FROM bench.v_provider_metrics GROUP BY data_year, provider_type
), st AS (
    SELECT data_year, provider_type, state,
           count(*)           AS providers,
           sum(total_payment) AS payment,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY payment_per_bene) AS median_ppb
    FROM bench.v_provider_metrics GROUP BY data_year, provider_type, state
)
SELECT st.*, nat.national_median_ppb,
       st.median_ppb / NULLIF(nat.national_median_ppb, 0) AS index_vs_national
FROM st JOIN nat USING (data_year, provider_type);

-- Robust (median/MAD) z-score on log payment-per-beneficiary within specialty-year.
-- A statistical flag for review, NOT evidence of anything improper.
CREATE VIEW bench.v_provider_outliers AS
WITH base AS (
    SELECT *, ln(payment_per_bene) AS lppb
    FROM bench.v_provider_metrics WHERE payment_per_bene > 0
), med AS (
    SELECT data_year, provider_type, count(*) AS peers,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY lppb) AS med_l
    FROM base GROUP BY data_year, provider_type HAVING count(*) >= 30
), mad AS (
    SELECT m.data_year, m.provider_type, m.peers, m.med_l,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY abs(b.lppb - m.med_l)) AS mad_l
    FROM base b JOIN med m USING (data_year, provider_type)
    GROUP BY m.data_year, m.provider_type, m.peers, m.med_l
)
SELECT b.data_year, b.npi, b.provider_type, b.state, b.total_benes,
       b.total_payment, b.payment_per_bene,
       b.drug_payment / NULLIF(b.total_payment, 0) AS drug_share,   -- NULL = suppressed by CMS
       exp(mad.med_l) AS specialty_median_ppb,
       mad.peers,
       0.6745 * (b.lppb - mad.med_l) / NULLIF(mad.mad_l, 0) AS robust_z
FROM base b JOIN mad USING (data_year, provider_type);
