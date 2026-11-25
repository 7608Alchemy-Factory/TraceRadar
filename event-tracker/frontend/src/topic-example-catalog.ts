import { categories, MAX_TOPIC_TAGS, type TopicCategory } from "./topic-categories.ts";

type ExampleNotes = {
  recommendedCheck: string;
  passCriteria: string;
  testCase: string;
};

export type TopicExample = {
  id: number;
  title: string;
  description: string;
  tags: TopicCategory[];
  exclusions: string[];
  notes: ExampleNotes;
};

const categoryByTitle = new Map(
  Object.entries(categories).map(([id, title]) => [title as string, id as TopicCategory]),
);

export function parseTopicExamples(source: string): TopicExample[] {
  const markdown = source.replace(/\r\n?/g, "\n");
  const headings = [...markdown.matchAll(/^(#{2,3}) (.+)$/gm)];
  const result: TopicExample[] = [];
  let section: TopicCategory | undefined;

  headings.forEach((heading, index) => {
    if (heading[1] === "##") {
      section = categoryByTitle.get(heading[2].trim());
      return;
    }
    const numbered = /^(\d+)\.\s+(.+)$/.exec(heading[2]);
    if (!numbered) return;

    const id = Number(numbered[1]);
    const title = numbered[2].trim();
    const invalid = (reason: string) => new Error(`Topic example ${id} (${title}): ${reason}`);
    if (id !== result.length + 1 || !section || !title || title.length > 200)
      throw invalid("expected a numbered title in its primary category.");

    const block = markdown.slice(heading.index + heading[0].length, headings[index + 1]?.index ?? markdown.length);
    const fields = [...block.matchAll(/^- \*\*([^*]+)\*\*[^\S\n]*(.*)$/gm)];
    const values = new Map(fields.map((field) => [field[1], field[2].trim()]));
    function required(key: string) {
      const value = values.get(key);
      if (!value) throw invalid(`missing ${key}.`);
      return value;
    }
    if (values.size !== 6 || fields.length !== 6) throw invalid("expected six distinct fields.");
    const description = required("追踪目标");
    if (description.length > 4000) throw invalid("tracking goal exceeds 4000 characters.");

    const labels = required("主题类型").split("、").map((label) => label.trim());
    const tags = labels.map((label) => {
      const category = categoryByTitle.get(label);
      if (!category) throw invalid(`unknown category ${label}.`);
      return category;
    });
    if (tags[0] !== section || tags.length > MAX_TOPIC_TAGS || new Set(tags).size !== tags.length)
      throw invalid("the first category must match the section, with no duplicates and at most three categories.");

    const exclusionIndex = fields.findIndex((field) => field[1] === "排除规则");
    if (exclusionIndex < 0 || values.get("排除规则")) throw invalid("use one indented bullet per exclusion.");
    const exclusionField = fields[exclusionIndex];
    const rules = block.slice(exclusionField.index + exclusionField[0].length, fields[exclusionIndex + 1]?.index ?? block.length).trimEnd();
    const exclusions = rules.split("\n").filter((line) => line.trim()).map((line) => {
      const rule = /^  - (.+)$/.exec(line)?.[1].trim();
      if (!rule || rule.length > 160) throw invalid("each exclusion must be an indented bullet of 1–160 characters.");
      return rule;
    });
    if (!exclusions.length || exclusions.length > 20 || new Set(exclusions.map((rule) => rule.toLowerCase())).size !== exclusions.length)
      throw invalid("expected 1–20 distinct exclusions.");

    result.push({
      id, title, description, tags, exclusions,
      notes: {
        recommendedCheck: required("推荐检查"),
        passCriteria: required("通过标准"),
        testCase: required("验收样例"),
      },
    });
  });

  for (const category of categoryByTitle.values()) {
    if (result.filter((item) => item.tags[0] === category).length !== 2)
      throw new Error(`Expected two topic examples under ${categories[category]}.`);
  }
  return result;
}
