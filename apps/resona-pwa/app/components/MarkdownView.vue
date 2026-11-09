<script setup lang="ts">
import { computed } from "vue";
import { markdownBlocks } from "~/utils/markdown";

// Read-only rendering of a profile's Markdown output (headings, paragraphs, lists).
// Text interpolation only, never v-html.
const props = defineProps<{ text: string }>();

const blocks = computed(() => markdownBlocks(props.text));
</script>

<template>
	<article class="md">
		<template
			v-for="(block, i) in blocks"
			:key="i"
		>
			<component
				:is="`h${Math.min(block.level + 1, 6)}`"
				v-if="block.kind === 'heading'"
				class="md__heading"
				:class="`md__heading--${block.level}`"
			>
				{{ block.text }}
			</component>
			<p
				v-else-if="block.kind === 'paragraph'"
				class="md__p"
			>
				<template
					v-for="(line, j) in block.lines"
					:key="j"
				>
					<br v-if="j > 0">{{ line }}
				</template>
			</p>
			<component
				:is="block.ordered ? 'ol' : 'ul'"
				v-else
				class="md__list"
			>
				<li
					v-for="(item, j) in block.items"
					:key="j"
				>
					{{ item }}
				</li>
			</component>
		</template>
	</article>
</template>

<style scoped>
.md { padding:16px; border:1px solid var(--border); border-radius:var(--r-md); background:var(--white); color:var(--fg1); font-family:var(--font-body); font-size:16px; line-height:1.6; overflow-wrap:anywhere; }
.md > :first-child { margin-top:0; }
.md > :last-child { margin-bottom:0; }
.md__heading { margin:20px 0 8px; font-weight:700; color:var(--fg1); line-height:1.3; }
.md__heading--1 { font-size:22px; }
.md__heading--2 { font-size:19px; }
.md__heading--3, .md__heading--4, .md__heading--5, .md__heading--6 { font-size:16px; }
.md__p { margin:0 0 12px; }
.md__list { margin:0 0 12px; padding-left:22px; }
</style>
