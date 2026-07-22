<script>
	import '@evidence-dev/tailwind/fonts.css';
	import '../app.css';
	import { EvidenceDefaultLayout } from '@evidence-dev/core-components';
	export let data;
</script>

<EvidenceDefaultLayout {data} title="LNG Nowcaster" builtWithEvidence={false} neverShowQueries={true}>
	<slot slot="content" />
</EvidenceDefaultLayout>
