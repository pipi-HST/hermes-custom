import type { GetServerSideProps } from 'next'

export const getServerSideProps: GetServerSideProps = async () => ({
  redirect: {
    destination: '/workspace?tab=workbench&mode=draft-review',
    permanent: false,
  },
})

export default function DraftsRedirectPage() {
  return null
}
