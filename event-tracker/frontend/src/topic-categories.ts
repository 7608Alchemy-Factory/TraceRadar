export const categories = {
  tech: "科技",
  education: "教育",
  travel: "出行",
  science: "科学",
  food: "饮食",
  law: "法律",
  sports: "体育",
  deals: "优惠",
  politics: "时政",
  community: "民生",
  international: "国际",
  business: "财经",
  environment: "环境",
  culture: "文化",
  health: "健康",
} as const;

export const MAX_TOPIC_TAGS = 3;
export type TopicCategory = keyof typeof categories;
