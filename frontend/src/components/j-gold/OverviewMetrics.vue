<script setup lang="ts">
import type { RadarFilter } from "@/composables/useJGoldResearch";

defineProps<{
  metrics: {
    monthly_count: number;
    broker_count: number;
    industry_count: number;
    new_count: number | null;
    new_change: number | null;
    consensus_count: number;
    warming_count: number | null;
    warming_three_months: Array<{ month: string; count: number | null }>;
    breakout_count: number;
    average_excess_20d: number | null;
    excess_sample_count: number;
  };
}>();
const emit = defineEmits<{ drill: [filter: RadarFilter] }>();

const definitions = {
  monthly: "当前筛选范围内按股票身份去重；券商和行业分别去重统计。",
  new: "本月出现且上月没有有效推荐记录的股票。",
  consensus: "本月获得不少于 5 家不同规范化券商推荐。",
  warming: "本月推荐券商数高于上月。",
  breakout: "最新可用后复权收盘价达到最近 60 个交易日新高。",
  excess: "股票 20 交易日收益减同期沪深 300 收益，仅统计双边数据完整样本。",
};
</script>

<template>
  <section class="metric-strip" aria-label="J金股总览指标">
    <article>
      <span>本月金股</span><strong>{{ metrics.monthly_count }}</strong
      ><small
        >覆盖 {{ metrics.broker_count }} 家券商 ·
        {{ metrics.industry_count }} 个有效行业</small
      ><button
        class="info"
        :title="definitions.monthly"
        aria-label="本月金股定义"
      >
        i
</button
      ><button class="drill" @click="emit('drill', 'monthly')">查看明细</button>
    </article>
    <article>
      <span>新晋金股</span><strong>{{ metrics.new_count ?? "—" }}</strong
      ><small
        >较上月
        {{
          metrics.new_change == null
            ? "不可比"
            : `${metrics.new_change >= 0 ? "+" : ""}${metrics.new_change}`
        }}</small
      ><button class="info" :title="definitions.new" aria-label="新晋金股定义">
        i
</button
      ><button class="drill" @click="emit('drill', 'new')">查看明细</button>
    </article>
    <article>
      <span>高共识金股</span><strong>{{ metrics.consensus_count }}</strong
      ><small>不少于 5 家券商</small
      ><button
        class="info"
        :title="definitions.consensus"
        aria-label="高共识金股定义"
      >
        i
</button
      ><button class="drill" @click="emit('drill', 'consensus')">
        查看明细
      </button>
    </article>
    <article>
      <span>推荐升温</span><strong>{{ metrics.warming_count ?? "—" }}</strong
      ><small
        >近 3 月升温：{{
          metrics.warming_three_months
            .map(
              (point) => `${point.month.slice(5, 7)}月 ${point.count ?? "—"}`,
            )
            .join(" · ")
        }}</small
      ><button
        class="info"
        :title="definitions.warming"
        aria-label="推荐升温定义"
      >
        i
</button
      ><button class="drill" @click="emit('drill', 'warming')">查看明细</button>
    </article>
    <article>
      <span>金股突破</span><strong>{{ metrics.breakout_count }}</strong
      ><small>后复权 60 日新高</small
      ><button
        class="info"
        :title="definitions.breakout"
        aria-label="金股突破定义"
      >
        i
</button
      ><button class="drill" @click="emit('drill', 'breakout')">
        查看明细
      </button>
    </article>
    <article class="accent">
      <span>20 日平均超额</span
      ><strong>{{
        metrics.average_excess_20d == null
          ? "—"
          : `${metrics.average_excess_20d > 0 ? "+" : ""}${metrics.average_excess_20d}%`
      }}</strong
      ><small>有效样本 {{ metrics.excess_sample_count }} · 沪深 300</small
      ><button
        class="info"
        :title="definitions.excess"
        aria-label="20 日平均超额定义"
      >
        i
</button
      ><button class="drill" @click="emit('drill', 'excess')">查看明细</button>
    </article>
  </section>
</template>

<style scoped>
.metric-strip {
  display: grid;
  grid-template-columns: repeat(6, minmax(145px, 1fr));
  overflow-x: auto;
  border-block: 1px solid var(--lk-border);
  background: var(--lk-surface);
}
article {
  position: relative;
  display: grid;
  min-height: 126px;
  align-content: center;
  gap: 7px;
  padding: 18px;
  border-right: 1px solid var(--lk-border);
}
article:last-child {
  border-right: 0;
}
span,
small {
  color: var(--lk-text-secondary);
  font-size: 0.76rem;
}
strong {
  font-size: 1.72rem;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.03em;
}
.accent strong {
  color: var(--lk-fortune);
}
.info {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 44px;
  height: 44px;
  border: 1px solid var(--lk-border);
  border-radius: 50%;
  color: var(--lk-text-secondary);
  background: transparent;
  cursor: help;
}
.drill {
  width: fit-content;
  min-height: 44px;
  padding: 0;
  border: 0;
  color: var(--lk-primary);
  background: transparent;
  font-size: 0.72rem;
  font-weight: 700;
  cursor: pointer;
}
@media (max-width: 860px) {
  .metric-strip {
    grid-template-columns: repeat(3, minmax(180px, 1fr));
  }
}
@media (max-width: 620px) {
  .metric-strip {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    overflow: visible;
  }
  article {
    min-height: 118px;
    padding: 16px 12px;
  }
  article:nth-child(even) {
    border-right: 0;
  }
}
</style>
